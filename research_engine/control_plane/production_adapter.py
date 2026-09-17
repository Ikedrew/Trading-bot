"""Injected policy adapter contract; no live runtime implementation."""
from typing import Any, Protocol


class PolicyAdapter(Protocol):
    """Return detached JSON state; validation must not mutate state.

    Setters do not establish verification: callers must read back independently.
    """

    def read_effective_state(self) -> dict[str, Any]:
        ...

    def validate_intended_state(self, intended: dict[str, Any]) -> None:
        ...

    def apply_effective_state(self, intended: dict[str, Any]) -> None:
        ...

    def restore_effective_state(self, previous: dict[str, Any]) -> None:
        ...

