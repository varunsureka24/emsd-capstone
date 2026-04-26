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


DEFAULT_SERIAL_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 115200

FORCE_SENSOR_PORT = "/dev/ttyUSB0"   # verify with: ls /dev/ttyUSB*
FORCE_SENSOR_BAUD = 115200

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
CONTACT_THRESHOLD = 2.0        # kg — light contact detected, stop Z
HARD_CONTACT_THRESHOLD = 8.0   # kg — dangerous overload, emergency stop
FORCE_DEBOUNCE_COUNT = 3       # consecutive readings required before acting
Z_TOUCH_STEP = 0.2
Z_TOUCH_FEED = 1000
Z_MAX_DESCENT = 8.0
Z_STEP_INTERVAL = 0.10

WELD_DWELL_TIME = 1.0


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

    def __init__(self, serial_port: str = "COM3", baud: int = DEFAULT_BAUD,
                 enable_grbl: bool = True, enable_force_sensor: bool = True,
                 enable_controller: bool = True, enable_camera: bool = True,
                 enable_laser: bool = True, enable_weld_relay: bool = True):
        super().__init__()

        self._serial_port = serial_port
        self._baud = baud

        self._enable_grbl = enable_grbl
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

        self._latest_force = None
        self._contact_counter = 0
        self._z_start_lowering = None
        self._last_z_step_time = 0.0

        self._weld_start_time = None
        self._z_raise_started = False
        self._z_lower_started = False
        self._move_just_started = False

        self._force_history = deque(maxlen=5)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        log.info("WeldController starting...")
        try:
            if self._enable_grbl:
                self._init_grbl()
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
            log.error("Init failed: %s", exc)
            self._sm.post_event(Event.INIT_FAILED)
            self.log_message.emit(f"Init failed: {exc}")
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
        self._travel_height = max(5.0, min(80.0, height_mm))
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
        self.log_message.emit("Homing... ($H)")
        resp = self._grbl.home()
        self.log_message.emit(f"GRBL: {resp}")

    def prepare_weld_queue(self) -> None:
        self._weld_queue = list(self._waypoints.get_all())
        self._current_wp_index = 0
        self.log_message.emit(f"Weld queue loaded: {len(self._weld_queue)} waypoints.")
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
        if self._current_wp_index >= len(self._weld_queue):
            self._sm.post_event(Event.SEQUENCE_COMPLETE)
            return

        wp = self._weld_queue[self._current_wp_index]

        if self._grbl:
            self._grbl.move_to(z=self._travel_height, feed=Z_FEED_RATE)
            self._grbl.move_to(x=wp.x, y=wp.y, feed=XY_FEED_RATE)
            self._move_just_started = True

        self.log_message.emit(
            f"Moving to waypoint {self._current_wp_index + 1}/{len(self._weld_queue)}: "
            f"X={wp.x:.2f}, Y={wp.y:.2f}"
        )

    def _on_enter_z_lowering(self) -> None:
        self._contact_counter = 0
        self._last_z_step_time = 0.0
        self._z_lower_started = False

        pos = self._grbl.get_position() if self._grbl else None
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self._z_start_lowering = self._sim_z
        else:
            self._z_start_lowering = self._sim_z

        self.log_message.emit("Searching for contact with force sensor...")

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

        now = time.time()
        hat = (data["hat_x"], data["hat_y"])

        # Thumbstick takes priority over D-pad when active
        using_stick = data["stick_x"] != 0.0 or data["stick_y"] != 0.0
        if using_stick:
            if now - self._last_stick_jog_time < STICK_COMMAND_INTERVAL:
                return
            stick_mag = min((data["stick_x"] ** 2 + data["stick_y"] ** 2) ** 0.5, 1.0)
            feed = int(STICK_MIN_FEED + (STICK_MAX_FEED - STICK_MIN_FEED) * (stick_mag ** STICK_CURVE))
            step = (feed / 60.0) * STICK_COMMAND_INTERVAL * STICK_OVERLAP
            # Normalize direction so step distance always fills the interval —
            # prevents tiny steps at small deflections that execute fast and leave idle gaps
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
                    # Sync position from GRBL every 200 ms during stick jogging.
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
            return

        if self._move_just_started:
            self._move_just_started = False
            return

        pos = self._grbl.get_position()
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        state = self._grbl.get_machine_state()
        if state == "Idle":
            self._sm.post_event(Event.POSITION_REACHED)

    def _tick_fine_positioning(self) -> None:
        self._sm.post_event(Event.FINE_POS_DONE)

    def _tick_z_lowering(self) -> None:
        if not self._grbl:
            return

        pos = self._grbl.get_position()
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        if not self._force_sensor:
            if not self._z_lower_started:
                target_z = self._z_start_lowering - Z_MAX_DESCENT
                self._grbl.move_to(z=target_z, feed=Z_FEED_RATE)
                self._z_lower_started = True
                return

            state = self._grbl.get_machine_state()
            if state == "Idle":
                self.log_message.emit("Z lowered to max descent (no force sensor)")
                self._sm.post_event(Event.Z_LOWER_DONE)
            return

        if self._latest_force is not None:
            if self._latest_force >= HARD_CONTACT_THRESHOLD:
                self.log_message.emit("Hard contact threshold reached")
                self._grbl.feed_hold()
                self._sm.post_event(Event.ERROR_OCCURRED)
                return

            if self._latest_force >= CONTACT_THRESHOLD:
                self._contact_counter += 1
            else:
                self._contact_counter = 0

            if self._contact_counter >= FORCE_DEBOUNCE_COUNT:
                self.log_message.emit("Contact confirmed — weld about to fire")
                self._grbl.feed_hold()
                self._sm.post_event(Event.Z_LOWER_DONE)
                return

        if self._z_start_lowering is not None:
            if abs(self._sim_z - self._z_start_lowering) >= Z_MAX_DESCENT:
                self.log_message.emit("No contact detected before Z limit")
                self._grbl.feed_hold()
                self._sm.post_event(Event.ERROR_OCCURRED)
                return

        now = time.time()
        if now - self._last_z_step_time >= Z_STEP_INTERVAL:
            self._grbl.jog(dz=-Z_TOUCH_STEP, feed=Z_TOUCH_FEED)
            self._last_z_step_time = now

    def _tick_execute_weld(self) -> None:
        if self._weld_start_time is None:
            return

        if time.time() - self._weld_start_time >= WELD_DWELL_TIME:
            self._set_weld_relay(False)
            self.log_message.emit("Weld relay OFF")
            self._sm.post_event(Event.WELD_COMPLETE)

    def _tick_z_raising(self) -> None:
        if not self._grbl:
            return

        if not self._z_raise_started:
            self._grbl.move_to(z=self._travel_height, feed=Z_FEED_RATE)
            self._z_raise_started = True
            return

        pos = self._grbl.get_position()
        if pos is not None:
            self._sim_x, self._sim_y, self._sim_z = pos
            self.position_updated.emit(self._sim_x, self._sim_y, self._sim_z)

        state = self._grbl.get_machine_state()
        if state == "Idle":
            self._current_wp_index += 1
            if self._current_wp_index >= len(self._weld_queue):
                self._sm.post_event(Event.SEQUENCE_COMPLETE)
            else:
                self._sm.post_event(Event.Z_RAISE_DONE)

    def _tick_estop(self) -> None:
        pass

    def _tick_error(self) -> None:
        self._set_weld_relay(False)
        self._set_laser(False)