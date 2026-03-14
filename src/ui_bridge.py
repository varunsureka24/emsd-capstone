"""
ui_bridge.py — WebSocket bridge between the weld controller and the browser UI.

Runs an async WebSocket server on the Pi.  The browser connects and receives:
  • state_change   — current FSM state name
  • position       — current toolhead X/Y/Z from GRBL
  • waypoints      — full waypoint list after any mutation
  • error          — error messages

The browser sends back:
  • {"action": "enter_jog"}          — "Select Weld Points" button
  • {"action": "exit_jog"}           — back to IDLE
  • {"action": "start_sequence"}     — "Execute Weld Sequence" button
  • {"action": "estop"}              — emergency stop
  • {"action": "reset"}              — acknowledge error / reset
  • {"action": "home"}               — homing cycle
  • {"action": "set_point"}          — save current pos as waypoint
  • {"action": "remove_point", "index": N}

Requires: pip install websockets
"""

import asyncio
import inspect
import json
import logging
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765


class UIBridge:
    """Async WebSocket server for the browser-based operator UI."""

    def __init__(
        self,
        event_callback,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        """
        Args:
            event_callback: async or sync callable(action: str, payload: dict)
                            called when the browser sends an action.
            host: bind address.
            port: WebSocket port.
        """
        self._event_callback = event_callback
        self._host = host
        self._port = port
        self._clients: set = set()
        self._server = None

    async def start(self) -> None:
        """Start the WebSocket server (non-blocking)."""
        try:
            import websockets
        except ImportError:
            log.error("websockets not installed — run: pip install websockets")
            return

        self._server = await websockets.serve(
            self._handler, self._host, self._port
        )
        log.info("UI WebSocket server listening on ws://%s:%d", self._host, self._port)

    async def _handler(self, websocket, path=None) -> None:
        """Handle a single browser connection."""
        self._clients.add(websocket)
        remote = websocket.remote_address
        log.info("UI client connected: %s", remote)

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    action = msg.get("action", "")
                    log.debug("UI action: %s  payload: %s", action, msg)
                    await self._dispatch(action, msg)
                except json.JSONDecodeError:
                    log.warning("Bad JSON from UI: %s", raw[:100])
        except Exception as exc:
            log.info("UI client disconnected: %s (%s)", remote, exc)
        finally:
            self._clients.discard(websocket)

    async def _dispatch(self, action: str, payload: dict) -> None:
        """Route a UI action to the controller's event callback."""
        if inspect.iscoroutinefunction(self._event_callback):
            await self._event_callback(action, payload)
        else:
            self._event_callback(action, payload)

    # ── Broadcast to all connected UIs ────────────────────────────────

    def broadcast(self, message: dict) -> None:
        """Queue a JSON message to all connected browser clients."""
        if not self._clients:
            return
        data = json.dumps(message)
        for ws in list(self._clients):
            asyncio.ensure_future(self._safe_send(ws, data))

    async def _safe_send(self, ws, data: str) -> None:
        try:
            await ws.send(data)
        except Exception:
            self._clients.discard(ws)

    # ── Convenience broadcast methods ─────────────────────────────────

    def send_state(self, state_name: str) -> None:
        self.broadcast({"type": "state_change", "state": state_name})

    def send_position(self, x: float, y: float, z: float) -> None:
        self.broadcast({
            "type": "position",
            "x": round(x, 2),
            "y": round(y, 2),
            "z": round(z, 2),
        })

    def send_waypoints(self, waypoints_json: list[dict]) -> None:
        self.broadcast({"type": "waypoints", "points": waypoints_json})

    def send_error(self, message: str) -> None:
        self.broadcast({"type": "error", "message": message})
