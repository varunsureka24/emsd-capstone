"""
weld_controller.py — Main orchestrator for the spot-welding system.

Wires together:
  • WeldStateMachine      (state_machine.py)
  • GrblConnection        (grbl_comm.py — your existing serial layer)
  • XboxController        (xbox_input.py — to be implemented)
  • WaypointStore         (waypoints.py)
  • UI bridge             (ui_bridge.py — websocket push to the web frontend)

Run this on the Raspberry Pi as the top-level entry point.

Usage:
    python weld_controller.py [--port /dev/ttyUSB0] [--baud 115200]
"""

import argparse
import asyncio
import logging
import signal
import sys
from typing import Optional

from state_machine import WeldStateMachine, State, Event
from waypoints import WaypointStore, Waypoint

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD        = 115200
Z_TRAVEL_HEIGHT     = 10.0   # mm — safe retract height for X/Y rapids
Z_WELD_HEIGHT       = 0.0    # mm — contact height (tune during bring-up)
XY_FEED_RATE        = 3000   # mm/min for rapid positioning
Z_FEED_RATE         = 300    # mm/min for Z moves


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class WeldController:
    """
    Top-level controller.  Owns the state machine and reacts to
    state transitions by issuing GRBL commands and UI updates.
    """

    def __init__(self, serial_port: str, baud: int) -> None:
        # Core subsystems (lazy-init in start())
        self._serial_port = serial_port
        self._baud = baud
        self._grbl = None           # GrblConnection instance
        self._xbox = None           # XboxController instance
        self._ui = None             # UIBridge instance

        # State machine
        self._sm = WeldStateMachine(initial_state=State.SYSTEM_INIT)
        self._sm.on_transition(self._on_transition)

        # Waypoint management
        self._waypoints = WaypointStore()
        self._weld_queue: list[Waypoint] = []
        self._current_wp_index: int = 0

        # Async event for clean shutdown
        self._shutdown = asyncio.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize all subsystems, then enter the main loop."""
        log.info("WeldController starting…")

        try:
            await self._init_grbl()
            await self._init_xbox()
            await self._init_ui()
            self._sm.post_event(Event.INIT_COMPLETE)
        except Exception as exc:
            log.error("Init failed: %s", exc)
            self._sm.post_event(Event.INIT_FAILED)
            return

        await self._run_loop()

    async def _run_loop(self) -> None:
        """Main async loop — poll inputs and drive the FSM."""
        log.info("Entering main loop (state=%s)", self._sm.state.name)

        while not self._shutdown.is_set():
            state = self._sm.state

            if state == State.IDLE:
                await self._tick_idle()

            elif state == State.MANUAL_JOG:
                await self._tick_manual_jog()

            elif state == State.CAMERA_LASER_TARGETING:
                await self._tick_camera_targeting()

            elif state == State.MOVE_TO_POSITION:
                await self._tick_move_to_position()

            elif state == State.FINE_POSITIONING:
                await self._tick_fine_positioning()

            elif state == State.Z_LOWERING:
                await self._tick_z_lowering()

            elif state == State.EXECUTE_WELD:
                await self._tick_execute_weld()

            elif state == State.Z_RAISING:
                await self._tick_z_raising()

            elif state == State.EMERGENCY_STOP:
                await self._tick_estop()

            elif state == State.ERROR:
                await self._tick_error()

            # Yield to the event loop (controls tick rate ~50 Hz)
            await asyncio.sleep(0.02)

    def request_shutdown(self) -> None:
        self._shutdown.set()

    # ── Subsystem init stubs ──────────────────────────────────────────
    # Replace these with real implementations as each subsystem comes online.

    async def _init_grbl(self) -> None:
        """Open serial port and wait for GRBL welcome string."""
        log.info("Connecting to GRBL on %s @ %d …", self._serial_port, self._baud)
        # TODO: replace with your GrblConnection from grbl_comm.py
        # from grbl_comm import GrblConnection
        # self._grbl = GrblConnection(self._serial_port, self._baud)
        # await self._grbl.connect()
        log.warning("GRBL stub — no real connection yet")

    async def _init_xbox(self) -> None:
        """Open the Xbox controller input device."""
        log.info("Initializing Xbox controller…")
        # TODO: replace with XboxController from xbox_input.py
        log.warning("Xbox stub — no real connection yet")

    async def _init_ui(self) -> None:
        """Start the websocket server for the web frontend."""
        log.info("Initializing UI bridge…")
        # TODO: replace with UIBridge from ui_bridge.py
        log.warning("UI bridge stub — no real connection yet")

    # ── State-transition callback ─────────────────────────────────────

    def _on_transition(self, old: State, event: Event, new: State) -> None:
        """
        Fires every time the FSM changes state.
        Use for one-shot actions on state *entry*.
        """
        log.info("TRANSITION: %s --%s--> %s", old.name, event.name, new.name)

        # Push state change to the web UI
        self._broadcast_state(new)

        # --- Entry actions ---
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

    # ── Entry actions (one-shot on state entry) ───────────────────────

    def _on_enter_estop(self) -> None:
        """Immediately halt all motion."""
        log.critical("E-STOP ACTIVATED — sending GRBL reset")
        # self._grbl.send_realtime(b'\x18')  # Ctrl-X soft reset
        # self._grbl.send_realtime(b'!')     # Feed hold (belt-and-suspenders)

    def _on_enter_move_to_position(self) -> None:
        """Load the next waypoint and issue the X/Y rapid move."""
        if self._current_wp_index >= len(self._weld_queue):
            log.info("All waypoints welded — sequence complete")
            self._sm.post_event(Event.SEQUENCE_COMPLETE)
            return

        wp = self._weld_queue[self._current_wp_index]
        log.info(
            "Moving to waypoint %d/%d: (%.2f, %.2f)",
            self._current_wp_index + 1, len(self._weld_queue), wp.x, wp.y,
        )
        # Ensure Z is at travel height, then rapid to X/Y
        # self._grbl.send_gcode(f"G90 G0 Z{Z_TRAVEL_HEIGHT}")
        # self._grbl.send_gcode(f"G90 G0 X{wp.x} Y{wp.y} F{XY_FEED_RATE}")

    def _on_enter_z_lowering(self) -> None:
        """Lower Z axis to weld contact height."""
        log.info("Lowering Z to weld height %.2f mm", Z_WELD_HEIGHT)
        # self._grbl.send_gcode(f"G90 G1 Z{Z_WELD_HEIGHT} F{Z_FEED_RATE}")

    def _on_enter_execute_weld(self) -> None:
        """Fire the weld relay (M3 spindle-on → SpnEn active-LOW)."""
        log.info("Firing weld pulse via relay (M3)")
        # self._grbl.send_gcode("M3 S1000")  # SpnEn goes LOW → relay triggers kWeld
        # Weld duration is handled by kWeld hardware; we wait a fixed dwell:
        # self._grbl.send_gcode("G4 P0.5")   # 500 ms dwell
        # self._grbl.send_gcode("M5")         # SpnEn goes HIGH → relay off

    def _on_enter_z_raising(self) -> None:
        """Retract Z to travel height."""
        log.info("Raising Z to travel height %.2f mm", Z_TRAVEL_HEIGHT)
        # self._grbl.send_gcode(f"G90 G0 Z{Z_TRAVEL_HEIGHT}")

    def _on_enter_set_weld_point(self) -> None:
        """Capture current GRBL position and store as a waypoint."""
        # pos = self._grbl.query_position()  # Returns (x, y, z) tuple
        # wp = Waypoint(x=pos[0], y=pos[1])
        # self._waypoints.add(wp)
        # log.info("Saved waypoint: %s (total: %d)", wp, len(self._waypoints))
        log.warning("SET_WELD_POINT stub — no GRBL position query yet")
        self._sm.post_event(Event.WELD_POINT_SAVED)

    # ── Tick functions (called each loop iteration per state) ─────────

    async def _tick_idle(self) -> None:
        """Poll UI for button presses (jog mode, start sequence)."""
        # Events will be posted by the UI bridge or Xbox listener
        pass

    async def _tick_manual_jog(self) -> None:
        """Read Xbox joystick axes → send GRBL jog commands."""
        # TODO: read xbox axes, deadzone filter, send $J= jog commands
        # Example jog command:  $J=G91 X{dx} Y{dy} F{feed}
        pass

    async def _tick_camera_targeting(self) -> None:
        """Overlay laser crosshair and camera feed — mostly UI-side."""
        pass

    async def _tick_move_to_position(self) -> None:
        """Poll GRBL status until position is reached."""
        # status = self._grbl.query_status()
        # if status == "Idle":
        #     self._sm.post_event(Event.POSITION_REACHED)
        pass

    async def _tick_fine_positioning(self) -> None:
        """Optional fine-tune step — auto-advance for now."""
        # If no fine positioning logic, immediately advance:
        self._sm.post_event(Event.FINE_POS_DONE)

    async def _tick_z_lowering(self) -> None:
        """Poll GRBL status until Z move is complete."""
        # status = self._grbl.query_status()
        # if status == "Idle":
        #     self._sm.post_event(Event.Z_LOWER_DONE)
        pass

    async def _tick_execute_weld(self) -> None:
        """Wait for weld dwell to finish."""
        # status = self._grbl.query_status()
        # if status == "Idle":
        #     self._sm.post_event(Event.WELD_COMPLETE)
        pass

    async def _tick_z_raising(self) -> None:
        """Poll GRBL status; advance waypoint index on completion."""
        # status = self._grbl.query_status()
        # if status == "Idle":
        #     self._current_wp_index += 1
        #     if self._current_wp_index >= len(self._weld_queue):
        #         self._sm.post_event(Event.SEQUENCE_COMPLETE)
        #     else:
        #         self._sm.post_event(Event.Z_RAISE_DONE)
        pass

    async def _tick_estop(self) -> None:
        """Wait for e-stop release (physical button or UI)."""
        pass

    async def _tick_error(self) -> None:
        """Display error info; wait for user acknowledgment."""
        pass

    # ── Helpers ────────────────────────────────────────────────────────

    def _broadcast_state(self, state: State) -> None:
        """Push state update to the web frontend via websocket."""
        # self._ui.broadcast({"type": "state_change", "state": state.name})
        pass

    def prepare_weld_queue(self) -> None:
        """
        Copy stored waypoints into the execution queue.
        Call this before posting START_WELD_SEQUENCE.
        Optionally apply nearest-neighbor ordering here later.
        """
        self._weld_queue = list(self._waypoints.get_all())
        self._current_wp_index = 0
        log.info("Weld queue loaded: %d waypoints", len(self._weld_queue))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main(port: str, baud: int) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    controller = WeldController(serial_port=port, baud=baud)

    # Graceful shutdown on Ctrl-C
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, controller.request_shutdown)

    await controller.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spot-welder controller")
    parser.add_argument("--port", default=DEFAULT_SERIAL_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = parser.parse_args()

    asyncio.run(main(args.port, args.baud))
