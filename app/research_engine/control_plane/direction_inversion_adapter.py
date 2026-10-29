"""Wave 5.3 concrete production PolicyAdapter for direction_inversion.

Satisfies research_engine.control_plane.production_adapter.PolicyAdapter
exactly. Supports NORMAL + direction_inversion; rejects everything else.
Persists via core.optimisation_policy atomically; validates without
mutating; reads back independently. Importing/constructing never mutates.
No live trading calls here; never touches orders, scheduler, or startup.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core import optimisation_policy as _policy


class DirectionInversionPolicyAdapter:
    def __init__(self, policy_path=None):
        self._path = Path(policy_path) if policy_path is not None else _policy._DEFAULT_PATH

    @property
    def policy_path(self) -> Path:
        return self._path

    def read_effective_state(self) -> dict[str, Any]:
        state = _policy.read_policy_file(self._path)
        return json.loads(_policy.canonical(state))

    def validate_intended_state(self, intended: dict[str, Any]) -> None:
        _policy.validate_effective_state(json.loads(_policy.canonical(intended)))

    def apply_effective_state(self, intended: dict[str, Any]) -> None:
        cleaned = _policy.validate_effective_state(json.loads(_policy.canonical(intended)))
        # This is the sole production writer for this material policy. Keep
        # its local atomic file replacement from crossing a candidate's final
        # baseline/config validation-to-status commit boundary.
        from research_engine.v10.baselines.baseline_authority import (
            candidate_activation_guard,
        )

        with candidate_activation_guard():
            _policy.write_policy_file(cleaned, self._path)

    def restore_effective_state(self, previous: dict[str, Any]) -> None:
        cleaned = _policy.validate_effective_state(json.loads(_policy.canonical(previous)))
        from research_engine.v10.baselines.baseline_authority import (
            candidate_activation_guard,
        )

        with candidate_activation_guard():
            _policy.write_policy_file(cleaned, self._path)
