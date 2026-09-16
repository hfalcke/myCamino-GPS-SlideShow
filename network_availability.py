# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-wide, failure-aware network suspension for optional online work."""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
import weakref
from collections.abc import Callable


OFFLINE_GRACE_SECONDS = 5.0
ONLINE_STABILITY_SECONDS = 5.0
PROBE_INTERVAL_SECONDS = 1.0


def is_connectivity_error(error: BaseException) -> bool:
    """Distinguish loss of connectivity from HTTP policy/server responses."""
    if isinstance(error, urllib.error.HTTPError):
        return False
    if isinstance(error, urllib.error.URLError):
        reason = getattr(error, "reason", None)
        return isinstance(reason, (OSError, TimeoutError)) or reason is None
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    name = type(error).__name__.casefold()
    return name in {
        "connectionerror",
        "connecttimeout",
        "readtimeout",
        "newconnectionerror",
        "name resolution error",
    }


class NetworkAvailabilityCoordinator:
    """Suspend optional requests after sustained failures and resume them safely."""

    def __init__(
        self,
        *,
        grace_seconds: float = OFFLINE_GRACE_SECONDS,
        stability_seconds: float = ONLINE_STABILITY_SECONDS,
        probe: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.grace_seconds = max(0.0, float(grace_seconds))
        self.stability_seconds = max(0.0, float(stability_seconds))
        self._probe = probe or self._route_probe
        self._clock = clock
        self._condition = threading.Condition()
        self._state = "online"
        self._failure_since: float | None = None
        self._recovery_since: float | None = None
        self._listeners: list[weakref.ReferenceType] = []
        self._monitor: threading.Thread | None = None
        self._stopped = False

    @staticmethod
    def _route_probe() -> bool:
        # UDP connect performs no request; it only asks the operating system
        # whether a usable outward route currently exists.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("1.1.1.1", 53))
            return bool(probe.getsockname()[0])
        except OSError:
            return False
        finally:
            probe.close()

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def offline(self) -> bool:
        return self.state in {"offline", "recovering"}

    def add_listener(self, callback: Callable[[str], None]) -> None:
        try:
            reference = weakref.WeakMethod(callback)
        except TypeError:
            reference = weakref.ref(callback)
        with self._condition:
            self._listeners.append(reference)
            # Detect an already-offline machine even before the first optional
            # service happens to make a request.
            self._ensure_monitor_locked()

    def remove_listener(self, callback: Callable[[str], None]) -> None:
        with self._condition:
            self._listeners = [
                reference
                for reference in self._listeners
                if (target := reference()) is not None and target != callback
            ]

    def _set_state(self, state: str) -> None:
        listeners = []
        with self._condition:
            if state == self._state:
                return
            self._state = state
            self._condition.notify_all()
            retained = []
            for reference in self._listeners:
                callback = reference()
                if callback is not None:
                    retained.append(reference)
                    listeners.append(callback)
            self._listeners = retained
        for callback in listeners:
            try:
                callback(state)
            except Exception:
                pass

    def report_success(self) -> None:
        with self._condition:
            if self._state == "online":
                self._failure_since = None
                return
            if self._recovery_since is None:
                self._recovery_since = self._clock()

    def report_failure(self, error: BaseException) -> None:
        if not is_connectivity_error(error):
            return
        now = self._clock()
        should_mark_offline = False
        with self._condition:
            self._recovery_since = None
            if self._failure_since is None:
                self._failure_since = now
            if now - self._failure_since >= self.grace_seconds:
                should_mark_offline = True
            self._ensure_monitor_locked()
        if should_mark_offline:
            self._set_state("offline")

    def wait_until_available(self, cancel_event=None) -> bool:
        """Wait only after Offline is established; return false when cancelled."""
        with self._condition:
            while self._state in {"offline", "recovering"} and not self._stopped:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                self._condition.wait(0.2)
            return not self._stopped

    def _ensure_monitor_locked(self) -> None:
        if self._monitor is not None and self._monitor.is_alive():
            return
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="mycamino-network-monitor",
            daemon=True,
        )
        self._monitor.start()

    def _monitor_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopped:
                    return
                failure_since = self._failure_since
                state = self._state
            now = self._clock()
            route_available = bool(self._probe())
            if not route_available:
                with self._condition:
                    self._recovery_since = None
                    if self._failure_since is None:
                        self._failure_since = now
                    failure_since = self._failure_since
                if failure_since is not None and now - failure_since >= self.grace_seconds:
                    self._set_state("offline")
            elif state in {"offline", "recovering"}:
                if state == "offline":
                    with self._condition:
                        self._recovery_since = now
                    self._set_state("recovering")
                else:
                    with self._condition:
                        recovery_since = self._recovery_since or now
                    if now - recovery_since >= self.stability_seconds:
                        with self._condition:
                            self._failure_since = None
                            self._recovery_since = None
                        self._set_state("online")
                        return
            elif failure_since is not None and now - failure_since >= self.grace_seconds:
                # A route can exist while DNS or the upstream connection is
                # unavailable. Sustained real request failures still suspend.
                self._set_state("offline")
            elif failure_since is None:
                return
            time.sleep(PROBE_INTERVAL_SECONDS)


network_availability = NetworkAvailabilityCoordinator()
