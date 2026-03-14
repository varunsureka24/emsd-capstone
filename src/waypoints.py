"""
waypoints.py — Weld-point storage and management.

Stores waypoints as (x, y) coordinates in machine space (mm).
Supports save/load to JSON for persistence across sessions.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import logging
import os
import time

log = logging.getLogger(__name__)

DEFAULT_WAYPOINTS_FILE = "waypoints.json"


@dataclass
class Waypoint:
    """A single weld target in machine coordinates."""
    x: float
    y: float
    label: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        tag = f" [{self.label}]" if self.label else ""
        return f"Waypoint({self.x:.2f}, {self.y:.2f}{tag})"


class WaypointStore:
    """
    Ordered collection of waypoints with file-backed persistence.

    Methods mirror the UI actions from the whiteboard sketch:
      add()       — "Set Point" button
      remove()    — delete a stored point
      clear()     — wipe all points
      get_all()   — feed into the weld execution queue
      reorder()   — manual drag-reorder in UI (or nearest-neighbor later)
    """

    def __init__(self, filepath: str = DEFAULT_WAYPOINTS_FILE) -> None:
        self._points: list[Waypoint] = []
        self._filepath = filepath

    # ── CRUD ──────────────────────────────────────────────────────────

    def add(self, wp: Waypoint) -> int:
        """Append a waypoint. Returns its index."""
        self._points.append(wp)
        idx = len(self._points) - 1
        log.info("Added waypoint %d: %s", idx, wp)
        return idx

    def remove(self, index: int) -> Optional[Waypoint]:
        """Remove waypoint by index. Returns the removed point or None."""
        if 0 <= index < len(self._points):
            removed = self._points.pop(index)
            log.info("Removed waypoint %d: %s", index, removed)
            return removed
        log.warning("Remove failed — index %d out of range", index)
        return None

    def clear(self) -> int:
        """Remove all waypoints. Returns the count cleared."""
        count = len(self._points)
        self._points.clear()
        log.info("Cleared %d waypoints", count)
        return count

    def get(self, index: int) -> Optional[Waypoint]:
        if 0 <= index < len(self._points):
            return self._points[index]
        return None

    def get_all(self) -> list[Waypoint]:
        """Return a copy of the waypoint list (safe for iteration)."""
        return list(self._points)

    def __len__(self) -> int:
        return len(self._points)

    # ── Reordering ────────────────────────────────────────────────────

    def reorder(self, new_order: list[int]) -> bool:
        """
        Reorder waypoints by index list.
        new_order must be a permutation of range(len(self)).
        """
        if sorted(new_order) != list(range(len(self._points))):
            log.warning("Invalid reorder permutation: %s", new_order)
            return False
        self._points = [self._points[i] for i in new_order]
        log.info("Waypoints reordered: %s", new_order)
        return True

    # ── Persistence ───────────────────────────────────────────────────

    def save(self, filepath: Optional[str] = None) -> None:
        path = filepath or self._filepath
        data = [asdict(wp) for wp in self._points]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        log.info("Saved %d waypoints to %s", len(data), path)

    def load(self, filepath: Optional[str] = None) -> int:
        path = filepath or self._filepath
        if not os.path.exists(path):
            log.info("No waypoints file found at %s", path)
            return 0
        with open(path, "r") as f:
            data = json.load(f)
        self._points = [
            Waypoint(
                x=d["x"],
                y=d["y"],
                label=d.get("label"),
                created_at=d.get("created_at", time.time()),
            )
            for d in data
        ]
        log.info("Loaded %d waypoints from %s", len(self._points), path)
        return len(self._points)

    # ── Serialization for UI ──────────────────────────────────────────

    def to_json_list(self) -> list[dict]:
        """Serializable list for pushing to the web frontend."""
        return [
            {
                "index": i,
                "x": round(wp.x, 2),
                "y": round(wp.y, 2),
                "label": wp.label,
            }
            for i, wp in enumerate(self._points)
        ]
