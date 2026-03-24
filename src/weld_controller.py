"""
weld_controller.py — orchestrator for the spot-welding system.

Runs as a QObject with a 50 Hz QTimer (replaces the asyncio loop).
Instantiated and owned by SpotWelderGUI in pi_gui.py.

Integration points (all marked TODO):
  - GRBL serial:  replace _init_grbl / jog / home stub calls with grbl_comm.py
  - Xbox input:   replace _tick_manual_jog stub with xbox_input.py polling
"""

import logging
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from state_machine import WeldStateMachine, State, Event
from waypoints import WaypointStore, Waypoint

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD        = 115200
Z_TRAVEL_HEIGHT     = 10.0   # mm — safe retract height for X/Y rapids
Z_WELD_HEIGHT       = 0.0    # mm — contact height (tune during bring-up)
XY_FEED_RATE        = 3000   # mm/min for rapid positioning
Z_FEED_RATE         = 300    # mm/min for Z moves
JOG_STEP_XY         = 1.0   # mm per jog button press
JOG_STEP_Z          = 0.5   # mm per Z jog button press
JOG_FEED            = 500   # mm/min for manual jog


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class WeldController(QObject):
    """
    Top-level controller.  Owns the state machine and reacts to
    state transitions by issuing GRBL commands and emitting Qt signals.
    """

    # Signals consumed by the GUI
    state_changed     = pyqtSignal(str)                  # new State.name
    position_updated  = pyqtSignal(float, float, float)  # x, y, z (mm)
    waypoints_updated = pyqtSignal(list)                 # list of waypoint dicts
    progress_updated  = pyqtSignal(int, int)             # current_index, total
    log_message       = pyqtSignal(str)                  # text for message log

    def __init__(self, serial_port: str = DEFAULT_SERIAL_PORT, baud: int = DEFAULT_BAUD):
        super().__init__()
        self._serial_port = serial_port
        self._baud = baud
        self._grbl = None   # TODO: GrblConnection from grbl_comm.py
        self._xbox = None   # TODO: XboxController from xbox_input.py

        self._sm = WeldStateMachine(initial_state=State.SYSTEM_INIT)
        self._sm.on_transition(self._on_transition)

        self._waypoints = WaypointStore()
        self._weld_queue: list[Waypoint] = []
        self._current_wp_index: int = 0

        # Simulated position — updated by jog() until real GRBL is wired in
        self._sim_x: float = 0.0
        self._sim_y: float = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Initialize subsystems and start the 50 Hz tick timer."""
        log.info("WeldController starting...")
        try:
            self._init_grbl()
            self._init_xbox()
            self._sm.post_event(Event.INIT_COMPLETE)
            self.log_message.emit("System initialized. Waiting for user input.")
            self.position_updated.emit(self._sim_x, self._sim_y, 0.0)
        except Exception as exc:
            log.error("Init failed: %s", exc)
            self._sm.post_event(Event.INIT_FAILED)
            self.log_message.emit(f"Init failed: {exc}")
            return
        self._timer.start(20)  # 50 Hz

    def stop(self) -> None:
        self._timer.stop()

    # ── Public API (called by GUI) ─────────────────────────────────────────

    def post_event(self, event: Event) -> bool:
        return self._sm.post_event(event)

    @property
    def state(self) -> State:
        return self._sm.state

    @property
    def waypoints(self) -> WaypointStore:
        return self._waypoints

    def jog(self, dx: float = 0, dy: float = 0) -> None:
        """Send a relative jog command to GRBL. Only valid in MANUAL_JOG state."""
        if self._sm.state != State.MANUAL_JOG:
            log.warning("Jog requested outside MANUAL_JOG state — ignored")
            return
        self._sim_x += dx
        self._sim_y += dy
        log.info("Simulated position: X=%.2f Y=%.2f", self._sim_x, self._sim_y)
        self.position_updated.emit(self._sim_x, self._sim_y, 0.0)
        # TODO: cmd = f"$J=G91 X{dx:.3f} Y{dy:.3f} F{JOG_FEED}"
        # TODO: self._grbl.send_gcode(cmd)

    def home(self) -> None:
        """Run GRBL homing cycle ($H)."""
        log.info("Homing...")
        self.log_message.emit("Homing... ($H)")
        # TODO: self._grbl.send_gcode("$H")

    def prepare_weld_queue(self) -> None:
        """Copy stored waypoints into the execution queue, then reset the index."""
        self._weld_queue = list(self._waypoints.get_all())
        self._current_wp_index = 0
        log.info("Weld queue loaded: %d waypoints", len(self._weld_queue))
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

    # ── Subsystem init stubs ───────────────────────────────────────────────

    def _init_grbl(self) -> None:
        log.warning("GRBL stub — no real connection yet")
        # TODO: from grbl_comm import GrblConnection
        # TODO: self._grbl = GrblConnection(self._serial_port, self._baud)
        # TODO: self._grbl.connect()

    def _init_xbox(self) -> None:
        log.warning("Xbox stub — no real connection yet")
        # TODO: from xbox_input import XboxController
        # TODO: self._xbox = XboxController()

    # ── State-transition callback ──────────────────────────────────────────

    def _on_transition(self, old: State, event: Event, new: State) -> None:
        log.info("TRANSITION: %s --%s--> %s", old.name, event.name, new.name)
        self.state_changed.emit(new.name)
        self.log_message.emit(f"{old.name}  →  {new.name}")
        self.progress_updated.emit(self._current_wp_index, len(self._weld_queue))

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

    # ── Entry actions (one-shot on state entry) ────────────────────────────

    def _on_enter_estop(self) -> None:
        log.critical("E-STOP ACTIVATED — sending GRBL reset")
        self.log_message.emit("*** E-STOP ACTIVATED ***")
        # TODO: self._grbl.send_realtime(b'\x18')  # Ctrl-X soft reset
        # TODO: self._grbl.send_realtime(b'!')      # Feed hold

    def _on_enter_move_to_position(self) -> None:
        if self._current_wp_index >= len(self._weld_queue):
            log.info("All waypoints welded — sequence complete")
            self._sm.post_event(Event.SEQUENCE_COMPLETE)
            return
        wp = self._weld_queue[self._current_wp_index]
        log.info("Moving to waypoint %d/%d: (%.2f, %.2f)",
                 self._current_wp_index + 1, len(self._weld_queue), wp.x, wp.y)
        self.log_message.emit(
            f"Moving to waypoint {self._current_wp_index + 1}/{len(self._weld_queue)}: "
            f"X={wp.x:.2f}, Y={wp.y:.2f}"
        )
        # TODO: self._grbl.send_gcode(f"G90 G0 Z{Z_TRAVEL_HEIGHT}")
        # TODO: self._grbl.send_gcode(f"G90 G0 X{wp.x} Y{wp.y} F{XY_FEED_RATE}")

    def _on_enter_z_lowering(self) -> None:
        log.info("Lowering Z to weld height %.2f mm", Z_WELD_HEIGHT)
        # TODO: self._grbl.send_gcode(f"G90 G1 Z{Z_WELD_HEIGHT} F{Z_FEED_RATE}")

    def _on_enter_execute_weld(self) -> None:
        log.info("Firing weld pulse via relay (M3)")
        self.log_message.emit(
            f"Welding at waypoint {self._current_wp_index + 1}/{len(self._weld_queue)}..."
        )
        # TODO: self._grbl.send_gcode("M3 S1000")  # SpnEn LOW → relay triggers kWeld
        # TODO: self._grbl.send_gcode("G4 P0.5")   # 500 ms dwell
        # TODO: self._grbl.send_gcode("M5")         # SpnEn HIGH → relay off

    def _on_enter_z_raising(self) -> None:
        log.info("Raising Z to travel height %.2f mm", Z_TRAVEL_HEIGHT)
        # TODO: self._grbl.send_gcode(f"G90 G0 Z{Z_TRAVEL_HEIGHT}")

    def _on_enter_set_weld_point(self) -> None:
        # TODO: when GRBL is connected, replace _sim_x/_sim_y with:
        # TODO: pos = self._grbl.query_position(); x, y = pos[0], pos[1]
        wp = Waypoint(x=self._sim_x, y=self._sim_y)
        self._waypoints.add(wp)
        log.info("Saved waypoint: %s (total: %d)", wp, len(self._waypoints))
        self.log_message.emit(
            f"Waypoint {len(self._waypoints)} saved: X={self._sim_x:.2f}, Y={self._sim_y:.2f}"
        )
        self._sm.post_event(Event.WELD_POINT_SAVED)
        self.waypoints_updated.emit(self._waypoints.to_json_list())

    # ── Tick functions (called every 20 ms by QTimer) ──────────────────────

    def _tick(self) -> None:
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

    def _tick_idle(self) -> None:
        pass

    def _tick_manual_jog(self) -> None:
        # TODO: read xbox axes, apply deadzone, call self.jog(dx, dy)
        pass

    def _tick_camera_targeting(self) -> None:
        pass

    def _tick_move_to_position(self) -> None:
        # TODO: status = self._grbl.query_status()
        # TODO: if status == "Idle": self._sm.post_event(Event.POSITION_REACHED)
        pass

    def _tick_fine_positioning(self) -> None:
        # No fine-positioning logic yet — advance immediately
        self._sm.post_event(Event.FINE_POS_DONE)

    def _tick_z_lowering(self) -> None:
        # TODO: status = self._grbl.query_status()
        # TODO: if status == "Idle": self._sm.post_event(Event.Z_LOWER_DONE)
        pass

    def _tick_execute_weld(self) -> None:
        # TODO: status = self._grbl.query_status()
        # TODO: if status == "Idle": self._sm.post_event(Event.WELD_COMPLETE)
        pass

    def _tick_z_raising(self) -> None:
        # TODO: status = self._grbl.query_status()
        # TODO: if status == "Idle":
        # TODO:     self._current_wp_index += 1
        # TODO:     if self._current_wp_index >= len(self._weld_queue):
        # TODO:         self._sm.post_event(Event.SEQUENCE_COMPLETE)
        # TODO:     else:
        # TODO:         self._sm.post_event(Event.Z_RAISE_DONE)
        pass

    def _tick_estop(self) -> None:
        pass

    def _tick_error(self) -> None:
        pass
