"""Event seam for optional run-completion integrations.

``run_completed`` receives the versioned run metadata plus ``speed_samples``:
a list of ``{"timestamp": float, "speed_mph": float}`` values containing only
accepted Hall-sensor samples. Implementations must keep network work out of the
GPIO callback path.
"""

from __future__ import annotations

from typing import Protocol


class EventPublisher(Protocol):
    def live_speed(self, payload: dict) -> None: ...

    def run_completed(self, payload: dict) -> None: ...

    def close(self) -> None: ...


class NullEventPublisher:
    def live_speed(self, payload: dict) -> None:
        return None

    def run_completed(self, payload: dict) -> None:
        return None

    def close(self) -> None:
        return None
