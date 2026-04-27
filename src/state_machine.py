"""
state_machine.py — Core finite state machine for the spot-welding system.

The SM is event-driven: external code (UI, controller, Xbox input) posts
events, and the SM looks up the (current_state, event) pair in the
transition table to decide the next state and run the associated action.

Usage:
    from state_machine import WeldStateMachine, State, Event

    sm = WeldStateMachine()
    sm.on_transition(callback)   # subscribe to state changes
    sm.post_event(Event.INIT_COMPLETE)
"""

from enum import Enum, auto
from typing import Callable, Optional
import logging
import time

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# States 
# ---------------------------------------------------------------------------
class State(Enum):
    # --- System lifecycle branch ---
    EMERGENCY_STOP          = auto()
    SYSTEM_INIT             = auto()
    ERROR                   = auto()
    IDLE                    = auto()

    # --- Manual targeting / waypoint-setting branch ---
    MANUAL_JOG              = auto()
    CAMERA_LASER_TARGETING  = auto()
    SET_WELD_POINT          = auto()

    # --- Automated weld-execution branch ---
    MOVE_TO_POSITION        = auto()
    FINE_POSITIONING        = auto()
    Z_LOWERING              = auto()
    EXECUTE_WELD            = auto()
    Z_RAISING               = auto()

    MANUAL_WELD             = auto()
    MANUAL_CALIBRATION      = auto()

    SET_TRAVEL_HEIGHT       = auto()


# ---------------------------------------------------------------------------
# Events  (the triggers that cause transitions)
# ---------------------------------------------------------------------------
class Event(Enum):
    """Inputs that drive state transitions."""
    # System lifecycle
    INIT_COMPLETE           = auto()
    INIT_FAILED             = auto()
    RESET                   = auto()

    # Emergency
    ESTOP_PRESSED           = auto()
    ESTOP_CLEARED           = auto()

    # Manual targeting flow
    ENTER_JOG_MODE          = auto()   # UI "Select Weld Points" button
    EXIT_JOG_MODE           = auto()   # User done jogging, back to IDLE
    TOGGLE_CAMERA_LASER     = auto()   # Switch between jog and cam/laser
    CONFIRM_WELD_POINT      = auto()   # Xbox A-button or UI "Set Point"
    WELD_POINT_SAVED        = auto()   # Point stored → back to MANUAL_JOG

    # Weld execution flow
    START_WELD_SEQUENCE     = auto()   # UI "Execute Weld Sequence" button
    POSITION_REACHED        = auto()   # X/Y rapid move complete
    FINE_POS_DONE           = auto()   # Fine adjustment complete
    Z_LOWER_DONE            = auto()   # Z at weld height
    WELD_COMPLETE           = auto()   # Weld pulse finished
    Z_RAISE_DONE            = auto()   # Z retracted to travel height
    SEQUENCE_COMPLETE       = auto()   # All waypoints welded
    NEXT_WAYPOINT           = auto()   # Advance to next point in queue

    # Error handling
    ERROR_OCCURRED          = auto()
    ERROR_ACKNOWLEDGED      = auto()

    # Manual Weld 
    ENTER_MANUAL_WELD       = auto()
    EXIT_MANUAL_WELD        = auto()
    MANUAL_WELD_TRIGGER     = auto()

    # Manual Homing
    ENTER_MANUAL_CALIBRATION = auto()
    EXIT_MANUAL_CALIBRATION = auto()

    # Set working Z height
    ENTER_SET_TRAVEL_HEIGHT = auto()
    EXIT_SET_TRAVEL_HEIGHT = auto()


# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------
# Dict of (State, Event) → State.
# If a pair isn't in the table the event is silently ignored (logged).

TRANSITIONS: dict[tuple[State, Event], State] = {
    # ── System init ──────────────────────────────────────────────────
    (State.SYSTEM_INIT,  Event.INIT_COMPLETE):      State.IDLE,
    (State.SYSTEM_INIT,  Event.INIT_FAILED):         State.ERROR,

    # ── IDLE  ────────────────────────────────────────────────────────
    (State.IDLE,         Event.ENTER_JOG_MODE):      State.MANUAL_JOG,
    (State.IDLE,         Event.START_WELD_SEQUENCE):  State.MOVE_TO_POSITION,

    # ── Manual jog / targeting branch ────────────────────────────────
    (State.MANUAL_JOG,           Event.EXIT_JOG_MODE):        State.IDLE,
    (State.MANUAL_JOG,           Event.TOGGLE_CAMERA_LASER):  State.CAMERA_LASER_TARGETING,
    (State.MANUAL_JOG,           Event.CONFIRM_WELD_POINT):   State.SET_WELD_POINT,
    (State.CAMERA_LASER_TARGETING, Event.TOGGLE_CAMERA_LASER): State.MANUAL_JOG,
    (State.CAMERA_LASER_TARGETING, Event.CONFIRM_WELD_POINT):  State.SET_WELD_POINT,
    (State.SET_WELD_POINT,       Event.WELD_POINT_SAVED):     State.MANUAL_JOG,

    # ── Weld execution branch ────────────────────────────────────────
    (State.MOVE_TO_POSITION, Event.POSITION_REACHED):   State.FINE_POSITIONING,
    (State.FINE_POSITIONING, Event.FINE_POS_DONE):       State.Z_LOWERING,
    (State.Z_LOWERING,       Event.Z_LOWER_DONE):        State.EXECUTE_WELD,
    (State.EXECUTE_WELD,     Event.WELD_COMPLETE):        State.Z_RAISING,
    (State.Z_RAISING,        Event.Z_RAISE_DONE):         State.MOVE_TO_POSITION,
    (State.Z_RAISING,        Event.SEQUENCE_COMPLETE):     State.IDLE,

    # ── Error recovery ───────────────────────────────────────────────
    (State.ERROR,            Event.ERROR_ACKNOWLEDGED):   State.SYSTEM_INIT,
    (State.ERROR,            Event.RESET):                State.SYSTEM_INIT,

    # ── Manual weld ───────────────────────────────────────────────
    (State.IDLE, Event.ENTER_MANUAL_WELD): State.MANUAL_WELD,
    (State.MANUAL_WELD, Event.EXIT_MANUAL_WELD): State.IDLE,

     # ── Manual homing ───────────────────────────────────────────────
    (State.IDLE, Event.ENTER_MANUAL_CALIBRATION): State.MANUAL_CALIBRATION, 
    (State.MANUAL_CALIBRATION, Event.EXIT_MANUAL_CALIBRATION): State.IDLE,

    (State.IDLE, Event.ENTER_SET_TRAVEL_HEIGHT): State.SET_TRAVEL_HEIGHT,
    (State.SET_TRAVEL_HEIGHT, Event.EXIT_SET_TRAVEL_HEIGHT): State.IDLE,

}

# E-stop can fire from ANY state (handled specially in post_event).
# ERROR_OCCURRED can fire from most operational states.
_ERROR_ELIGIBLE = {
    State.IDLE, State.MANUAL_JOG, State.CAMERA_LASER_TARGETING,
    State.SET_WELD_POINT, State.MOVE_TO_POSITION, State.FINE_POSITIONING,
    State.Z_LOWERING, State.EXECUTE_WELD, State.Z_RAISING,
}


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
TransitionCallback = Callable[[State, Event, State], None]


class WeldStateMachine:
    """Event-driven finite state machine for the weld system."""

    def __init__(self, initial_state: State = State.SYSTEM_INIT) -> None:
        self._state = initial_state
        self._listeners: list[TransitionCallback] = []
        self._entry_time: float = time.monotonic()
        log.info("FSM created in %s", self._state.name)

    # -- public API --------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    @property
    def time_in_state(self) -> float:
        """Seconds since the last state transition."""
        return time.monotonic() - self._entry_time

    def on_transition(self, cb: TransitionCallback) -> None:
        """Register a callback: cb(old_state, event, new_state)."""
        self._listeners.append(cb)

    def post_event(self, event: Event) -> bool:
        """
        Post an event to the state machine.
        Returns True if a transition occurred, False if the event was ignored.
        """
        old = self._state

        # --- Global overrides (any-state transitions) ---
        if event == Event.ESTOP_PRESSED:
            self._do_transition(old, event, State.EMERGENCY_STOP)
            return True

        if event == Event.ERROR_OCCURRED and old in _ERROR_ELIGIBLE:
            self._do_transition(old, event, State.ERROR)
            return True

        # E-stop release only valid when already in EMERGENCY_STOP
        if event == Event.ESTOP_CLEARED and old == State.EMERGENCY_STOP:
            self._do_transition(old, event, State.SYSTEM_INIT)
            return True

        # --- Normal table lookup ---
        new = TRANSITIONS.get((old, event))
        if new is None:
            log.debug("Ignored event %s in state %s", event.name, old.name)
            return False

        self._do_transition(old, event, new)
        return True

    # -- internals ---------------------------------------------------------

    def _do_transition(self, old: State, event: Event, new: State) -> None:
        self._state = new
        self._entry_time = time.monotonic()
        log.info("%s  --%s-->  %s", old.name, event.name, new.name)
        for cb in self._listeners:
            try:
                cb(old, event, new)
            except Exception:
                log.exception("Listener raised during transition")
