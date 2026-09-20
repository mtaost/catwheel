"""Future event seam for MQTT/notification integrations.

The logger currently uses the no-op publisher.  A future MQTT implementation
can implement this interface without coupling GPIO collection to the network.
"""

from __future__ import annotations

from typing import Protocol


class EventPublisher(Protocol):
    def live_speed(self, payload: dict) -> None: ...

    def run_completed(self, payload: dict) -> None: ...


class NullEventPublisher:
    def live_speed(self, payload: dict) -> None:
        return None

    def run_completed(self, payload: dict) -> None:
        return None
