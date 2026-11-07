"""The hackathon risk engine: a per-session violation counter inside the gateway.

Two policy denials inside the window mean the agent is following instructions it should not
(for example a poisoned product listing), so the session is revoked. EventBridge is P1.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class ViolationCounter:
    def __init__(self, threshold: int = 2, window_seconds: int = 300) -> None:
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._denials: dict[str, deque[float]] = defaultdict(deque)

    def record(self, sid: str, now: float | None = None) -> int:
        """Record one policy denial and return how many are inside the window."""
        now = time.monotonic() if now is None else now
        with self._lock:
            window = self._denials[sid]
            window.append(now)
            while window and now - window[0] > self.window_seconds:
                window.popleft()
            return len(window)

    def should_revoke(self, count: int) -> bool:
        return count >= self.threshold

    def forget(self, sid: str) -> None:
        with self._lock:
            self._denials.pop(sid, None)

    def reset(self) -> None:
        with self._lock:
            self._denials.clear()
