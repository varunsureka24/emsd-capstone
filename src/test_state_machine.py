"""
test_state_machine.py — Validates FSM transitions against the diagram.

Run:  python -m pytest test_state_machine.py -v
  or: python test_state_machine.py
"""

import sys
import os

# Allow running from src/ or project root
sys.path.insert(0, os.path.dirname(__file__))

from state_machine import WeldStateMachine, State, Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fresh_sm(initial: State = State.SYSTEM_INIT) -> WeldStateMachine:
    return WeldStateMachine(initial_state=initial)


def walk(sm: WeldStateMachine, events: list[Event]) -> list[State]:
    """Post a sequence of events and return the state after each."""
    states = []
    for ev in events:
        sm.post_event(ev)
        states.append(sm.state)
    return states


# ---------------------------------------------------------------------------
# Tests — System init & error recovery
# ---------------------------------------------------------------------------
def test_init_success():
    sm = fresh_sm()
    assert sm.state == State.SYSTEM_INIT
    sm.post_event(Event.INIT_COMPLETE)
    assert sm.state == State.IDLE


def test_init_failure():
    sm = fresh_sm()
    sm.post_event(Event.INIT_FAILED)
    assert sm.state == State.ERROR


def test_error_recovery_via_reset():
    sm = fresh_sm(State.ERROR)
    sm.post_event(Event.RESET)
    assert sm.state == State.SYSTEM_INIT


def test_error_recovery_via_ack():
    sm = fresh_sm(State.ERROR)
    sm.post_event(Event.ERROR_ACKNOWLEDGED)
    assert sm.state == State.SYSTEM_INIT


# ---------------------------------------------------------------------------
# Tests — Manual jog / waypoint-setting branch
# ---------------------------------------------------------------------------
def test_jog_mode_entry_and_exit():
    sm = fresh_sm(State.IDLE)
    sm.post_event(Event.ENTER_JOG_MODE)
    assert sm.state == State.MANUAL_JOG
    sm.post_event(Event.EXIT_JOG_MODE)
    assert sm.state == State.IDLE


def test_camera_laser_toggle():
    sm = fresh_sm(State.MANUAL_JOG)
    sm.post_event(Event.TOGGLE_CAMERA_LASER)
    assert sm.state == State.CAMERA_LASER_TARGETING
    sm.post_event(Event.TOGGLE_CAMERA_LASER)
    assert sm.state == State.MANUAL_JOG


def test_set_weld_point_from_jog():
    sm = fresh_sm(State.MANUAL_JOG)
    sm.post_event(Event.CONFIRM_WELD_POINT)
    assert sm.state == State.SET_WELD_POINT
    sm.post_event(Event.WELD_POINT_SAVED)
    assert sm.state == State.MANUAL_JOG


def test_set_weld_point_from_camera():
    sm = fresh_sm(State.CAMERA_LASER_TARGETING)
    sm.post_event(Event.CONFIRM_WELD_POINT)
    assert sm.state == State.SET_WELD_POINT
    sm.post_event(Event.WELD_POINT_SAVED)
    assert sm.state == State.MANUAL_JOG


# ---------------------------------------------------------------------------
# Tests — Full weld execution sequence
# ---------------------------------------------------------------------------
def test_single_weld_cycle():
    """IDLE → MOVE → FINE → Z_LOW → WELD → Z_RAISE → (next or done)"""
    sm = fresh_sm(State.IDLE)
    states = walk(sm, [
        Event.START_WELD_SEQUENCE,
        Event.POSITION_REACHED,
        Event.FINE_POS_DONE,
        Event.Z_LOWER_DONE,
        Event.WELD_COMPLETE,
        Event.SEQUENCE_COMPLETE,    # All waypoints done
    ])
    assert states == [
        State.MOVE_TO_POSITION,
        State.FINE_POSITIONING,
        State.Z_LOWERING,
        State.EXECUTE_WELD,
        State.Z_RAISING,
        State.IDLE,                 # Back to idle when sequence finishes
    ]


def test_multi_waypoint_cycle():
    """Two waypoints: after Z_RAISE, loop back to MOVE for the next."""
    sm = fresh_sm(State.IDLE)
    sm.post_event(Event.START_WELD_SEQUENCE)

    # First waypoint
    walk(sm, [
        Event.POSITION_REACHED,
        Event.FINE_POS_DONE,
        Event.Z_LOWER_DONE,
        Event.WELD_COMPLETE,
        Event.Z_RAISE_DONE,         # More waypoints remain → loop
    ])
    assert sm.state == State.MOVE_TO_POSITION

    # Second (final) waypoint
    walk(sm, [
        Event.POSITION_REACHED,
        Event.FINE_POS_DONE,
        Event.Z_LOWER_DONE,
        Event.WELD_COMPLETE,
        Event.SEQUENCE_COMPLETE,
    ])
    assert sm.state == State.IDLE


# ---------------------------------------------------------------------------
# Tests — Emergency stop from various states
# ---------------------------------------------------------------------------
def test_estop_from_idle():
    sm = fresh_sm(State.IDLE)
    sm.post_event(Event.ESTOP_PRESSED)
    assert sm.state == State.EMERGENCY_STOP


def test_estop_from_weld_execution():
    sm = fresh_sm(State.EXECUTE_WELD)
    sm.post_event(Event.ESTOP_PRESSED)
    assert sm.state == State.EMERGENCY_STOP


def test_estop_from_z_lowering():
    sm = fresh_sm(State.Z_LOWERING)
    sm.post_event(Event.ESTOP_PRESSED)
    assert sm.state == State.EMERGENCY_STOP


def test_estop_recovery():
    sm = fresh_sm(State.EMERGENCY_STOP)
    sm.post_event(Event.ESTOP_CLEARED)
    assert sm.state == State.SYSTEM_INIT


# ---------------------------------------------------------------------------
# Tests — Error from operational states
# ---------------------------------------------------------------------------
def test_error_from_manual_jog():
    sm = fresh_sm(State.MANUAL_JOG)
    sm.post_event(Event.ERROR_OCCURRED)
    assert sm.state == State.ERROR


def test_error_from_move():
    sm = fresh_sm(State.MOVE_TO_POSITION)
    sm.post_event(Event.ERROR_OCCURRED)
    assert sm.state == State.ERROR


# ---------------------------------------------------------------------------
# Tests — Invalid transitions are ignored
# ---------------------------------------------------------------------------
def test_ignored_event():
    sm = fresh_sm(State.IDLE)
    result = sm.post_event(Event.WELD_COMPLETE)  # Makes no sense in IDLE
    assert result is False
    assert sm.state == State.IDLE


def test_start_sequence_not_from_jog():
    sm = fresh_sm(State.MANUAL_JOG)
    result = sm.post_event(Event.START_WELD_SEQUENCE)
    assert result is False
    assert sm.state == State.MANUAL_JOG


# ---------------------------------------------------------------------------
# Run as script
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in test_funcs:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {passed + failed}")
    sys.exit(1 if failed else 0)
