import logging
import time
from collections import deque

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from state_machine import WeldStateMachine, State, Event
from waypoints import WaypointStore, Waypoint
from grbl_controller import GrblController
from camera_manager import CameraManager
from controller_input import ControllerInput
from force_sensor import ForceSensorReader

log = logging.getLogger(__name__)

try:
    from gpiozero import LED
except Exception:
    LED = None


DEFAULT_SERIAL_PORT = "COM3"
DEFAULT_BAUD = 115200

FORCE_SENSOR_PORT = "COM5"
FORCE_SENSOR_BAUD = 9600

CAMERA_INDEX = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480

# LASER / RELAY CONNECTION
LASER_GPIO_PIN = 18 
WELD_RELAY_GPIO_PIN = 23

# GRBL INFO
Z_TRAVEL_HEIGHT_DEFAULT = 25.0   # mm — fallback if user cancels the startup prompt
XY_FEED_RATE = 3000
Z_FEED_RATE = 1000

JOG_FEED = 2000
COMMAND_INTERVAL = 0.2
JOG_STEP_XY = COMMAND_INTERVAL * JOG_FEED / 60.0 * 0.5  # ~3.3 mm — 2-segment lookahead

STICK_COMMAND_INTERVAL = 0.02  # 50 ms for analog stick — smoother response
STICK_OVERLAP = 1.5             # step covers 1.5× the interval so planner buffer never empties
STICK_MIN_FEED = 1000           # mm/min — floor to keep motors above resonant frequency range
STICK_MAX_FEED = 5000           # mm/min — speed at full deflection
STICK_CURVE = 2.0               # response curve exponent: 1=linear, 2=quadratic, 3=more aggressive

# FORCE / Z_LOWERING THRESHOLDS (units: kg, matching Arduino output)
# With probe in air the reading will vary ~0.0–0.6 kg due to HX711 noise.
# Tune CONTACT_THRESHOLD after physical testing; it must sit comfortably
# above the at-rest noise floor.
CONTACT_THRESHOLD = 1.25       # kg — light contact detected, stop Z
HARD_CONTACT_THRESHOLD = 8.0   # kg — dangerous overload, emergency stop
FORCE_DEBOUNCE_COUNT = 2       # consecutive readings required before acting
Z_TOUCH_STEP = 0.2
Z_TOUCH_FEED = 1000
Z_MAX_DESCENT = 20.0
Z_STEP_INTERVAL = 0.12

WELD_DWELL_TIME = 0.100  # seconds — ensures relay fires for full 100 ms before Z raises

X_OFFSET = -7.5

class WeldController(QObject):
    state_changed = pyqtSignal(str)
    position_updated = pyqtSignal(float, float, float)
    waypoints_updated = pyqtSignal(list)
    progress_updated = pyqtSignal(int, int)
    log_message = pyqtSignal(str)

    controller_jog_visual = pyqtSignal(bool, bool, bool, bool)
    camera_frame_ready = pyqtSignal(object)
    laser_state_changed = pyqtSignal(bool)
    force_updated = pyqtSignal(float)

    def __init__(self, serial_port: str = DEFAULT_SERIAL_PORT, baud: int = DEFAULT_BAUD,
                 enable_grbl: bool = True, enable_homing: bool = True,
                 enable_force_sensor: bool = True,
                 enable_controller: bool = True, enable_camera: bool = True,
                 enable_laser: bool = True, enable_weld_relay: bool = True):
        super().__init__()

        self._serial_port = serial_port
        self._baud = baud

        self._enable_grbl = enable_grbl
        self._enable_homing = enable_homing
        self._enable_force_sensor = enable_force_sensor
        self._enable_controller = enable_controller
        self._enable_camera = enable_camera
        self._enable_laser = enable_laser
        self._enable_weld_relay = enable_weld_relay

        self._grbl = None
        self._controller_input = None
        self._force_sensor = None
        self._camera = None

        self._laser = None
        self._weld_relay = None

        self._sm = WeldStateMachine(initial_state=State.SYSTEM_INIT)
        self._sm.on_transition(self._on_transition)

        self._waypoints = WaypointStore()
        self._weld_queue: list[Waypoint] = []
        self._current_wp_index = 0

        self._sim_x = 0.0
        self._sim_y = 0.0
        self._sim_z = 0.0
        self._travel_height: float = Z_TRAVEL_HEIGHT_DEFAULT

        self._last_jog_time = 0.0
        self._last_stick_jog_time = 0.0
        self._last_stick_pos_sync_time = 0.0
        self._prev_hat = (0, 0)
        self._prev_stick_active = False
        self._prev_z_active = False

        self._latest_force = None
        self._contact_counter = 0
        self._z_start_lowering = None
        self._last_z_step_time = 0.0

        self._weld_start_time = None
        self._z_raise_started = False
        self._z_lower_started = False
        self._z_fast_lower_complete = False
        self._z_fast_lower_deadline = 0.0
        self._z_steps_taken = 0
        self._last_contact_time = 0.0
        self._move_just_started = False
        self._move_phase = "Z_RAISE"  # "Z_RAISE" | "XY_MOVE" | "DONE"
        self._working_height: float = 5.0  # Z where force-sensor descent begins (set by user)

        self._force_history = deque(maxlen=5)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._manual_weld_active = False

    def start(self) -> None:
        log.info("WeldController starting...")
        try:
            if self._enable_grbl:
                self._init_grbl()
            if self._enable_homing and self._grbl:
                self._run_homing()
            if self._enable_force_sensor:
                self._init_force_sensor()
            if self._enable_controller:
                self._init_controller()
            if self._enable_camera:
                self._init_camera()
            if self._enable_laser:
                self._init_laser()
            if self._enable_weld_relay:
                self._init_weld_relay()

            self._set_laser(False)
            self._set_weld_relay(False)

            self._sm.post_event(Event.INIT_COMPLETE)
            self.log_message.emit("System initialized. Waiting for user input.")
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        except Exception as exc:
            msg = str(exc)
            if "PermissionError" in msg or "Access is denied" in msg:
                msg += " — close Arduino IDE Serial Monitor or any other program using this port, then restart."
            log.error("Init failed: %s", msg)
            self._sm.post_event(Event.INIT_FAILED)
            self.log_message.emit(f"Init failed: {msg}")
            return

        self._timer.start(20)

    def stop(self) -> None:
        self._timer.stop()

        try:
            self._set_laser(False)
            self._set_weld_relay(False)
        except Exception:
            pass

        try:
            if self._camera:
                self._camera.stop()
        except Exception:
            pass

        try:
            if self._controller_input:
                self._controller_input.close()
        except Exception:
            pass

        try:
            if self._grbl:
                self._grbl.close()
        except Exception:
            pass

        try:
            if self._force_sensor:
                self._force_sensor.close()
        except Exception:
            pass

    def set_travel_height(self, height_mm: float) -> None:
        self._travel_height = height_mm
        self.log_message.emit(f"Travel height set to {self._travel_height:.1f} mm")

    @property
    def travel_height(self) -> float:
        return self._travel_height

    def post_event(self, event: Event) -> bool:
        return self._sm.post_event(event)

    @property
    def state(self) -> State:
        return self._sm.state

    @property
    def waypoints(self) -> WaypointStore:
        return self._waypoints

    def home(self) -> None:
        if not self._grbl:
            return
        self._run_homing()

    def _run_homing(self) -> None:
        self.log_message.emit("Homing... ($H)")
        resp = self._grbl.home()
        self.log_message.emit(f"GRBL homing response: {resp}")
        self._grbl.send_command("G10 L20 P1 X0 Y0 Z0")
        self._sim_x, self._sim_y, self._sim_z = 0.0, 0.0, 0.0
        self.position_updated.emit(0.0, 0.0, 0.0)
        self.log_message.emit("Position after home: X=0.00, Y=0.00, Z=0.00")

    def prepare_weld_queue(self) -> None:
        self._weld_queue = list(self._waypoints.get_all())
        self._current_wp_index = 0
        self.log_message.emit(f"Weld queue loaded: {len(self._weld_queue)} waypoints.")
        log.debug("[DBG] prepare_weld_queue: %d waypoints loaded", len(self._weld_queue))
        for i, wp in enumerate(self._weld_queue):
            log.debug("[DBG]   wp[%d]: x=%.2f y=%.2f", i, wp.x, wp.y)
        self.progress_updated.emit(0, len(self._weld_queue))

    def remove_waypoint(self, index: int) -> None:
        self._waypoints.remove(index)
        self.waypoints_updated.emit(self._waypoints.to_json_list())

    def clear_waypoints(self) -> None:
        self._waypoints.clear()
        self.waypoints_updated.emit(self._waypoints.to_json_list())

    def move_waypoint_up(self, index: int) -> None:
        if index <= 0 or index >= len(self._waypoints):
            return
        pts = list(range(len(self._waypoints)))
        pts[index], pts[index - 1] = pts[index - 1], pts[index]
        self._waypoints.reorder(pts)
        self.waypoints_updated.emit(self._waypoints.to_json_list())

    def move_waypoint_down(self, index: int) -> None:
        if index < 0 or index >= len(self._waypoints) - 1:
            return
        pts = list(range(len(self._waypoints)))
        pts[index], pts[index + 1] = pts[index + 1], pts[index]
        self._waypoints.reorder(pts)
        self.waypoints_updated.emit(self._waypoints.to_json_list())

    def _init_grbl(self) -> None:
        self._grbl = GrblController(self._serial_port, self._baud)
        self._grbl.connect()
        self.log_message.emit(f"Connected to GRBL on {self._serial_port}")

        pos = self._grbl.get_position()
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos

    def _init_force_sensor(self) -> None:
        self._force_sensor = ForceSensorReader(FORCE_SENSOR_PORT, FORCE_SENSOR_BAUD)
        self._force_sensor.connect()
        self.log_message.emit(f"Connected to force sensor on {FORCE_SENSOR_PORT}")

    def _init_controller(self) -> None:
        self._controller_input = ControllerInput()
        self._controller_input.connect()
        self.log_message.emit(f"Controller connected: {self._controller_input.get_name()}")

    def _init_camera(self) -> None:
        self._camera = CameraManager(CAMERA_INDEX, CAMERA_WIDTH, CAMERA_HEIGHT)
        self._camera.start()
        self.log_message.emit("Camera started")

    def _init_laser(self) -> None:
        if LED is None:
            self._laser = None
            return
        try:
            self._laser = LED(LASER_GPIO_PIN)
            self._laser.off()
        except Exception:
            self._laser = None

    def _init_weld_relay(self) -> None:
        # Relay is on the toolhead Arduino (pin 7); controlled via serial commands.
        # No GPIO init needed.
        pass

    def _set_laser(self, on: bool) -> None:
        try:
            if self._laser is None:
                self.laser_state_changed.emit(on)
                return
            if on:
                self._laser.on()
            else:
                self._laser.off()
            self.laser_state_changed.emit(on)
        except Exception:
            self.laser_state_changed.emit(on)

    def _set_weld_relay(self, on: bool) -> None:
        if not self._enable_weld_relay:
            return
        cmd = "WELD_ON" if on else "WELD_OFF"
        try:
            if self._force_sensor:
                self._force_sensor.send_command(cmd)
        except Exception:
            pass

    def _on_transition(self, old: State, event: Event, new: State) -> None:
        self.state_changed.emit(new.name)
        self.log_message.emit(f"{old.name}  →  {new.name}")
        self.progress_updated.emit(self._current_wp_index, len(self._weld_queue))

        self._set_laser(new == State.MANUAL_JOG)

        if new != State.MANUAL_JOG:
            self.controller_jog_visual.emit(False, False, False, False)

        if new == State.EMERGENCY_STOP:
            self._on_enter_estop()
        elif new == State.MOVE_TO_POSITION:
            self._on_enter_move_to_position()
        elif new == State.Z_LOWERING:
            self._on_enter_z_lowering()
        elif new == State.EXECUTE_WELD:
            self._on_enter_execute_weld()
        elif new == State.Z_RAISING:
            self._on_enter_z_raising()
        elif new == State.SET_WELD_POINT:
            self._on_enter_set_weld_point()

    def _on_enter_estop(self) -> None:
        self.log_message.emit("*** E-STOP ACTIVATED ***")
        self._set_laser(False)
        self._set_weld_relay(False)
        if self._grbl:
            self._grbl.feed_hold()
            self._grbl.soft_reset()

    def _on_enter_move_to_position(self) -> None:
        log.debug("[DBG] _on_enter_move_to_position: index=%d queue_len=%d grbl=%s",
                  self._current_wp_index, len(self._weld_queue), "connected" if self._grbl else "NONE")
        if self._current_wp_index >= len(self._weld_queue):
            self.log_message.emit(
                f"[DBG] Queue exhausted at index {self._current_wp_index}/{len(self._weld_queue)} — firing SEQUENCE_COMPLETE"
            )
            self._sm.post_event(Event.SEQUENCE_COMPLETE)
            return

        wp = self._weld_queue[self._current_wp_index]
        self.log_message.emit(
            f"Moving to waypoint {self._current_wp_index + 1}/{len(self._weld_queue)}: "
            f"Laser X={wp.x:.2f}, Y={wp.y:.2f} -> "
            f"Toolhead X={wp.x + X_OFFSET:.2f}, Y={wp.y:.2f}"
        )

        if not self._grbl:
            self.log_message.emit("[DBG] WARNING: GRBL not connected — move command skipped, sequence will stall")
            return

        pos = self._grbl.get_position()
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos

        dz = self._travel_height - self._sim_z
        if abs(dz) > 0.05:
            self._move_phase = "Z_RAISE"
            self._grbl.jog(dz=dz, feed=Z_FEED_RATE)
            log.debug("[DBG] Jogging Z: dz=%.2f to travel_height=%.2f", dz, self._travel_height)
        else:
            self._move_phase = "XY_MOVE"
            dx = (wp.x + X_OFFSET) - self._sim_x
            dy = wp.y - self._sim_y
            if abs(dx) > 0.05 or abs(dy) > 0.05:
                self._grbl.jog(dx=dx, dy=dy, feed=XY_FEED_RATE)
                log.debug("[DBG] Skipped Z (at height), jogging XY: dx=%.2f dy=%.2f", dx, dy)
            else:
                self._move_phase = "DONE"

        self._move_just_started = True

    def _on_enter_z_lowering(self) -> None:
        self._grbl.jog(dx=-5, dy=0.0, feed=XY_FEED_RATE)
        self._contact_counter = 0
        self._last_contact_time = 0.0
        self._last_z_step_time = 0.0
        self._z_lower_started = False
        self._z_fast_lower_complete = False
        self._z_steps_taken = 0

        pos = self._grbl.get_position() if self._grbl else None
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self._z_start_lowering = self._sim_z
        else:
            self._z_start_lowering = self._sim_z

        self.log_message.emit(
            f"Searching for contact with force sensor... "
            f"[DBG] z_start={self._z_start_lowering:.2f} grbl={'connected' if self._grbl else 'NONE'} "
            f"force_sensor={'connected' if self._force_sensor else 'NONE'} "
            f"latest_force={self._latest_force}"
        )

    def _on_enter_execute_weld(self) -> None:
        self._weld_start_time = time.time()
        self._set_weld_relay(True)
        prefix = "[DRY RUN] " if not self._enable_weld_relay else ""
        self.log_message.emit(
            f"{prefix}Weld relay ON at waypoint {self._current_wp_index + 1}/{len(self._weld_queue)}"
        )

    def _on_enter_z_raising(self) -> None:
        self._z_raise_started = False
        self._set_weld_relay(False)
        self.log_message.emit("Retracting Z to travel height...")

    def _on_enter_set_weld_point(self) -> None:
        pos = self._grbl.get_position() if self._grbl else None
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        wp = Waypoint(x=self._sim_x, y=self._sim_y)
        self._waypoints.add(wp)
        self.log_message.emit(
            f"Waypoint {len(self._waypoints)} saved: X={self._sim_x:.2f}, Y={self._sim_y:.2f}"
        )
        self._sm.post_event(Event.WELD_POINT_SAVED)
        self.waypoints_updated.emit(self._waypoints.to_json_list())
    
    def _start_manual_weld_from_current_pose(self) -> None:
        pos = self._grbl.get_position() if self._grbl else None
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        laser_wp = Waypoint(x=self._sim_x, y=self._sim_y)
        

        self._manual_weld_active = True
        self._weld_queue = [laser_wp]
        self._current_wp_index = 0

        self.log_message.emit(
            f"Manual weld target captured at pose: "
            f"X={laser_wp.x:.2f}, Y={laser_wp.y:.2f}"
        )

        self.progress_updated.emit(0, 1)
        self._sm.post_event(Event.MANUAL_WELD_TRIGGER)

    def _tick(self) -> None:
        self._tick_camera()
        self._tick_force_sensor()

        state = self._sm.state
        if state == State.IDLE:
            self._tick_idle()
        elif state == State.MANUAL_JOG:
            self._tick_manual_jog()
        elif state == State.CAMERA_LASER_TARGETING:
            self._tick_camera_targeting()
        elif state == State.MOVE_TO_POSITION:
            self._tick_move_to_position()
        elif state == State.FINE_POSITIONING:
            self._tick_fine_positioning()
        elif state == State.Z_LOWERING:
            self._tick_z_lowering()
        elif state == State.EXECUTE_WELD:
            self._tick_execute_weld()
        elif state == State.Z_RAISING:
            self._tick_z_raising()
        elif state == State.EMERGENCY_STOP:
            self._tick_estop()
        elif state == State.ERROR:
            self._tick_error()
        elif state == State.MANUAL_WELD:
            self._tick_manual_weld()
        elif state == State.SET_TRAVEL_HEIGHT:
            self._tick_set_travel_height()

    def _tick_camera(self) -> None:
        if not self._camera:
            return
        frame = self._camera.read_frame()
        if frame is not None:
            self.camera_frame_ready.emit(frame)

    def _tick_force_sensor(self) -> None:
        if not self._force_sensor:
            return
        val = self._force_sensor.read_latest()
        if val is not None:
            self._latest_force = val
            self._force_history.append(val)
            self.force_updated.emit(val)

    def _tick_idle(self) -> None:
        if not self._controller_input:
            return
        data = self._controller_input.poll()
        if data["btn_a"]:
            self._sm.post_event(Event.ENTER_JOG_MODE)

    def _tick_manual_jog(self) -> None:
        if not self._controller_input:
            return

        data = self._controller_input.poll()

        if data["btn_x"]:
            self._sm.post_event(Event.EXIT_JOG_MODE)
            return
        if data["btn_rt"]:
            self._sm.post_event(Event.CONFIRM_WELD_POINT)
            return

        self.controller_jog_visual.emit(
            data["left"], data["right"], data["up"], data["down"]
        )

        self._jog_from_controller_data(data)

    def _jog_from_controller_data(self, data: dict) -> None:
        now = time.time()
        hat = (data["hat_x"], data["hat_y"])

        using_stick = data["stick_x"] != 0.0 or data["stick_y"] != 0.0
        if using_stick:
            if now - self._last_stick_jog_time < STICK_COMMAND_INTERVAL:
                return
            stick_mag = min((data["stick_x"] ** 2 + data["stick_y"] ** 2) ** 0.5, 1.0)
            feed = int(STICK_MIN_FEED + (STICK_MAX_FEED - STICK_MIN_FEED) * (stick_mag ** STICK_CURVE))
            step = (feed / 60.0) * STICK_COMMAND_INTERVAL * STICK_OVERLAP
            dx = step * (data["stick_x"] / stick_mag)
            dy = step * (data["stick_y"] / stick_mag)
        else:
            if now - self._last_jog_time < COMMAND_INTERVAL:
                return
            dx = JOG_STEP_XY * data["hat_x"]
            dy = JOG_STEP_XY * data["hat_y"]
            feed = JOG_FEED

        if dx != 0.0 or dy != 0.0:
            if self._grbl:
                self._grbl.jog(dx=dx, dy=dy, feed=feed)
                if using_stick:
                    if now - self._last_stick_pos_sync_time >= 0.2:
                        pos = self._grbl.get_position()
                        if pos is not None:
                            self._sim_x, self._sim_y, self._sim_z = pos
                            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)
                        self._last_stick_pos_sync_time = now
                else:
                    pos = self._grbl.get_position()
                    if pos is not None:
                        self._sim_x, self._sim_y, self._sim_z = pos
                        self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

            if using_stick:
                self._last_stick_jog_time = now
            else:
                self._last_jog_time = now
        elif not using_stick and self._prev_stick_active:
            if self._grbl:
                self._grbl.jog_cancel()
                pos = self._grbl.get_position()
                if pos is not None:
                    self._sim_x, self._sim_y, self._sim_z = pos
                    self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)
        elif hat == (0, 0) and self._prev_hat != (0, 0):
            if self._grbl:
                self._grbl.jog_cancel()

        self._prev_hat = hat
        self._prev_stick_active = using_stick

    def _tick_camera_targeting(self) -> None:
        pass

    def _tick_move_to_position(self) -> None:
        if not self._grbl:
            log.debug("[DBG] _tick_move_to_position: GRBL not connected — stuck here")
            return

        if self._move_just_started:
            self._move_just_started = False
            return

        state, pos = self._grbl.get_status()
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        log.debug("[DBG] _tick_move_to_position: phase=%s grbl_state=%r", self._move_phase, state)

        if self._move_phase == "DONE" or state == "Idle":
            if self._move_phase == "Z_RAISE":
                wp = self._weld_queue[self._current_wp_index]
                dx = (wp.x + X_OFFSET) - self._sim_x
                dy = wp.y - self._sim_y
                self._move_phase = "XY_MOVE"
                self._move_just_started = True
                if abs(dx) > 0.05 or abs(dy) > 0.05:
                    self._grbl.jog(dx=dx, dy=dy, feed=XY_FEED_RATE)
                    self.log_message.emit(
                        f"Z raised to {self._sim_z:.2f} mm — jogging XY: dx={dx:.2f} dy={dy:.2f}"
                    )
            else:
                self._sm.post_event(Event.POSITION_REACHED)

    def _tick_fine_positioning(self) -> None:
        self._sm.post_event(Event.FINE_POS_DONE)

    def _tick_z_lowering(self) -> None:
        if not self._grbl:
            return

        # Phase 1: fast jog down to working height (get_position only once here)
        if not self._z_lower_started:
            pos = self._grbl.get_position()
            if pos is not None:
                self._sim_x, self._sim_y, self._sim_z = pos
                self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)
            dz = self._working_height - self._sim_z
            if dz < -0.05:
                self._grbl.jog(dz=dz, feed=Z_FEED_RATE)
                travel_secs = abs(dz) / (Z_FEED_RATE / 60.0)
                self._z_fast_lower_deadline = time.monotonic() + travel_secs + 0.3
                self.log_message.emit(
                    f"Fast lowering to working height {self._working_height:.1f} mm "
                    f"(~{travel_secs:.1f}s)..."
                )
            else:
                self._z_fast_lower_deadline = time.monotonic()
            self._z_lower_started = True
            return

        # Wait for fast lower to finish (time-based)
        if not self._z_fast_lower_complete:
            if time.monotonic() < self._z_fast_lower_deadline:
                return
            self._z_fast_lower_complete = True
            self.log_message.emit("At working height — starting force-sensor descent")
            if not self._force_sensor:
                self.log_message.emit("No force sensor — firing Z_LOWER_DONE at working height")
                self._sm.post_event(Event.Z_LOWER_DONE)
                return

        # Phase 2: slow step-jog with force sensor
        # Mirrors test_weld.py: abs(force), 1-second gap between contact readings,
        # step counter for Z limit (no blocking get_position() calls)
        now = time.time()
        force = self._latest_force

        if force is not None:
            if abs(force) >= HARD_CONTACT_THRESHOLD:
                self.log_message.emit(f"Hard contact threshold reached (force={force:.3f} kg)")
                self._grbl.feed_hold()
                self._sm.post_event(Event.ERROR_OCCURRED)
                return

            if abs(force) >= CONTACT_THRESHOLD:
                # Only increment once per second — matches test_weld.py's time.sleep(1.0)
                if now - self._last_contact_time >= 1.0:
                    self._contact_counter += 1
                    self._last_contact_time = now
                    log.debug("[DBG] Contact reading %d/%d (force=%.3f kg)",
                              self._contact_counter, FORCE_DEBOUNCE_COUNT, force)
            else:
                self._contact_counter = 0
                self._last_contact_time = 0.0

            if self._contact_counter >= FORCE_DEBOUNCE_COUNT:
                self.log_message.emit(f"Contact confirmed — weld about to fire (force={force:.3f} kg)")
                self._grbl.jog_cancel()
                self._sm.post_event(Event.Z_LOWER_DONE)
                return

        # Z limit via step count — avoids blocking position query
        descent = self._z_steps_taken * Z_TOUCH_STEP
        log.debug("[DBG] Z lowering step %d  descent=%.2f mm  force=%s",
                  self._z_steps_taken, descent, f"{force:.3f}" if force is not None else "None")
        if descent >= Z_MAX_DESCENT:
            self.log_message.emit(f"No contact before Z limit ({descent:.2f} mm descended)")
            self._grbl.feed_hold()
            self._sm.post_event(Event.ERROR_OCCURRED)
            return

        if now - self._last_z_step_time >= Z_STEP_INTERVAL:
            self._grbl.jog(dz=-Z_TOUCH_STEP, feed=Z_TOUCH_FEED)
            self._last_z_step_time = now
            self._z_steps_taken += 1

    def _tick_execute_weld(self) -> None:
        if self._weld_start_time is None:
            return

        if time.time() - self._weld_start_time >= WELD_DWELL_TIME:
            self.log_message.emit("Weld complete")
            self._sm.post_event(Event.WELD_COMPLETE)

    def _tick_z_raising(self) -> None:
        if not self._grbl:
            return

        if not self._z_raise_started:
            pos = self._grbl.get_position()
            if pos is not None:
                self._sim_x, self._sim_y, self._sim_z = pos
            dz = self._travel_height - self._sim_z
            if abs(dz) > 0.05:
                self._grbl.jog(dz=dz, feed=Z_FEED_RATE)
                log.debug("[DBG] _tick_z_raising: jogging Z dz=%.2f to travel_height=%.2f", dz, self._travel_height)
            self._z_raise_started = True
            return

        state, pos = self._grbl.get_status()
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        log.debug("[DBG] _tick_z_raising: grbl_state=%r z=%.2f travel_height=%.2f", state, self._sim_z, self._travel_height)
        if state == "Idle":
            self._current_wp_index += 1

            if self._manual_weld_active:
                self._manual_weld_active = False
                self._weld_queue = []
                self._current_wp_index = 0
                self.progress_updated.emit(0, 0)
                self._sm._do_transition(self._sm.state, Event.SEQUENCE_COMPLETE, State.MANUAL_WELD)
                return

            if self._current_wp_index >= len(self._weld_queue):
                self._sm.post_event(Event.SEQUENCE_COMPLETE)
            else:
                self._sm.post_event(Event.Z_RAISE_DONE)

    def _tick_estop(self) -> None:
        pass

    def _tick_error(self) -> None:
        self._set_weld_relay(False)
        self._set_laser(False)
    
    def _tick_manual_weld(self) -> None:
        if not self._controller_input:
            return

        data = self._controller_input.poll()

        # A and X do nothing in manual weld mode.
        # Right trigger captures current laser pose and starts one weld cycle.
        if data["btn_rt"]:
            self._start_manual_weld_from_current_pose()
            return

        self._jog_from_controller_data(data)

    def _tick_set_travel_height(self) -> None:
        if not self._controller_input:
            return

        data = self._controller_input.poll()

        if data["btn_x"]:
            self._sm.post_event(Event.EXIT_SET_TRAVEL_HEIGHT)
            return

        if data["btn_rt"]:
            pos = self._grbl.get_position() if self._grbl else None
            if pos is not None:
                self._sim_x, self._sim_y, self._sim_z = pos
                self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)
            self._working_height = self._sim_z
            self.set_travel_height(self._sim_z + 30.0)
            if self._grbl:
                self._grbl.jog(dz=20.0, feed=Z_FEED_RATE)
            self.log_message.emit(
                f"Working height set to {self._working_height:.1f} mm — "
                f"raising to travel height {self._travel_height:.1f} mm"
            )
            self._sm.post_event(Event.EXIT_SET_TRAVEL_HEIGHT)
            return

        now = time.time()
        using_z = data["up"] or data["down"]

        if not using_z:
            if self._grbl and self._prev_z_active:
                self._grbl.jog_cancel()
            self._prev_z_active = False
            return

        if now - self._last_stick_jog_time < STICK_COMMAND_INTERVAL:
            return

        z_feed = Z_FEED_RATE
        z_step = (z_feed / 60.0) * STICK_COMMAND_INTERVAL * STICK_OVERLAP
        dz = z_step if data["up"] else -z_step

        if self._grbl:
            self._grbl.jog(dz=dz, feed=z_feed)
            if now - self._last_stick_pos_sync_time >= 0.2:
                pos = self._grbl.get_position()
                if pos is not None:
                    self._sim_x, self._sim_y, self._sim_z = pos
                    self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)
                self._last_stick_pos_sync_time = now

        self._prev_z_active = True
        self._last_stick_jog_time = now