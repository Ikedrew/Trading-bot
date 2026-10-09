"""GENERIC candidate runtime manager (Block 1). Linked observation state."""

from __future__ import annotations

import logging
from typing import Any

from core.shadow.candidate_models import (
    CANDIDATE_RUNTIME_VERSION,
    CANDIDATE_STATE_ACTIVE,
    CANDIDATE_STATE_CLOSED,
    CANDIDATE_STATE_INVALID,
    CANDIDATE_MODEL_VERSION,
    SCHEMA_VERSION,
    CandidateIdentity,
    CandidateState,
    TreatmentBar,
    compute_candidate_r,
)
from core.shadow.candidate_persistence import (
    CandidateEventWriter,
    load_candidate_events,
)


class CandidateRegistration:

    def __init__(self, *, candidate_id: str, policy_id: str,
                 treatment_hash: str, adapter, entry_geometry=None,
                 provenance=None, experiment_id: str = "",
                 required_experiment_arm: str = "CANDIDATE",
                 activation_frontier_epoch_s: int = 0,
                 minimum_sample_requirement: int | None = None,
                 readiness_criteria=None) -> None:
        self.candidate_id = candidate_id
        self.policy_id = policy_id
        self.treatment_hash = treatment_hash
        self.adapter = adapter
        self.entry_geometry = dict(entry_geometry or {})
        self.provenance = dict(provenance or {})
        self.experiment_id = str(experiment_id or "")
        self.required_experiment_arm = str(required_experiment_arm or "")
        self.activation_frontier_epoch_s = int(activation_frontier_epoch_s or 0)
        self.minimum_sample_requirement = minimum_sample_requirement
        self.readiness_criteria = dict(readiness_criteria or {})


class CandidateRuntime:

    def __init__(self, writer=None) -> None:
        self._writer = writer or CandidateEventWriter()
        self._registrations: dict = {}
        self._adapters: dict = {}
        self._active: dict = {}
        self._closed: set = set()
        self._invalid: dict = {}
        self._close_count: dict = {}
        self._degraded: dict = {}
        self._integrity_failures: dict = {}
        self._canonical_bindings: dict = {}
        self._recovered = False

    def register(self, reg: CandidateRegistration) -> bool:
        if not reg.candidate_id or not reg.policy_id or not reg.treatment_hash:
            return False
        key = (str(reg.candidate_id), str(reg.policy_id))
        if key in self._registrations:
            existing = self._registrations[key]
            if (str(existing.treatment_hash) != str(reg.treatment_hash)
                    or type(existing.adapter) is not type(reg.adapter)):
                raise ValueError("CANDIDATE_REGISTRATION_CONFLICT")
            return True
        self._registrations[key] = reg
        self._recovered = False
        return True

    def registered_keys(self) -> list:
        return list(self._registrations.keys())

    def registration(self, candidate_id: str, policy_id: str):
        return self._registrations.get((str(candidate_id), str(policy_id)))

    def registrations(self) -> list:
        return list(self._registrations.values())

    def bind_shadow_lifecycle(self, *, shadow_trade_id: str,
                              canonical_opportunity_id: str,
                              trade_horizon: str, symbol: str,
                              direction: str, entry_time: int,
                              entry_price: float, stop_loss: float,
                              take_profit: float, experiment_id: str = "",
                              experiment_arm: str = "",
                              arm_assignment: dict | None = None,
                              baseline_lineage: dict | None = None,
                              baseline_open_event_id: str = "",
                              treatment_context: dict | None = None,
                              candidate_id: str, policy_id: str) -> str:
        if not canonical_opportunity_id or not shadow_trade_id:
            raise ValueError("CANDIDATE_CANONICAL_IDENTITY_MISSING")
        if not candidate_id or not policy_id:
            raise ValueError("CANDIDATE_POLICY_IDENTITY_INVALID")
        key = (str(candidate_id), str(policy_id))
        reg = self._registrations.get(key)
        if reg is None:
            raise ValueError("CANDIDATE_NOT_REGISTERED")
        if int(entry_time) < int(reg.activation_frontier_epoch_s or 0):
            raise ValueError("CANDIDATE_PRE_BIND_EVIDENCE_INELIGIBLE")
        if (
            experiment_arm
            and reg.required_experiment_arm
            and experiment_arm != reg.required_experiment_arm
        ):
            raise ValueError("CANDIDATE_EXPERIMENT_ARM_NOT_ELIGIBLE")
        if experiment_id and reg.experiment_id and experiment_id != reg.experiment_id:
            raise ValueError("CANDIDATE_EXPERIMENT_ID_MISMATCH")
        ident = CandidateIdentity(
            shadow_trade_id=shadow_trade_id,
            canonical_opportunity_id=canonical_opportunity_id,
            trade_horizon=trade_horizon, candidate_id=candidate_id,
            policy_id=policy_id, treatment_hash=reg.treatment_hash)
        reason = ident.validate()
        if reason:
            raise ValueError(reason)
        canonical_key = (
            str(canonical_opportunity_id), str(trade_horizon or "").upper(),
            str(candidate_id), str(policy_id),
        )
        existing_rid = self._canonical_bindings.get(canonical_key)
        if existing_rid:
            return str(existing_rid)
        rid = ident.runtime_id
        if rid in self._active or rid in self._closed:
            return rid
        risk = abs(float(entry_price) - float(stop_loss))
        if not (risk > 0):
            raise ValueError("CANDIDATE_GEOMETRY_INVALID")
        entry_geometry = {
            "entry_price": float(entry_price),
            "stop_loss": float(stop_loss),
            "take_profit": float(take_profit),
            "risk_distance": risk,
        }
        entry_geometry.update(reg.entry_geometry)
        entry_geometry.update(dict(treatment_context or {}))
        try:
            init_state = dict(reg.adapter.initialize(
                entry_geometry=dict(entry_geometry)) or {})
        except Exception as exc:
            raise ValueError(f"CANDIDATE_TREATMENT_NON_EVALUABLE:{exc}")
        st = CandidateState(
            candidate_id=candidate_id, policy_id=policy_id,
            treatment_hash=reg.treatment_hash,
            shadow_trade_id=shadow_trade_id,
            canonical_opportunity_id=canonical_opportunity_id,
            trade_horizon=str(trade_horizon or ""), symbol=symbol,
            direction=str(direction or "").upper(), entry_time=int(entry_time),
            entry_price=float(entry_price),
            original_stop_loss=float(stop_loss),
            original_take_profit=float(take_profit),
            original_risk_distance=risk,
            treatment_state=init_state,
            experiment_id=str(experiment_id or ""),
            experiment_arm=str(experiment_arm or ""),
            lineage={
                "registration_provenance": dict(reg.provenance or {}),
                "readiness_criteria": dict(reg.readiness_criteria or {}),
                "minimum_sample_requirement": reg.minimum_sample_requirement,
                "experiment_arm_assignment": dict(arm_assignment or {}),
                "baseline_open_event_id": str(baseline_open_event_id or ""),
                "baseline_record_lineage": dict(baseline_lineage or {}),
            },
        )
        self._active[rid] = st
        self._canonical_bindings[canonical_key] = rid
        self._adapters[rid] = reg.adapter
        ok = self._emit("CANDIDATE_OPEN", st, bar_time=int(entry_time or 0),
                        diagnostic="")
        if not ok:
            self._degraded[rid] = "CANDIDATE_PERSISTENCE_FAILED"
            st.state = CANDIDATE_STATE_INVALID
            self._invalid[rid] = "CANDIDATE_PERSISTENCE_FAILED"
            self._integrity_failures[rid] = {
                "candidate_id": st.candidate_id,
                "policy_id": st.policy_id,
                "reason": "CANDIDATE_PERSISTENCE_FAILED",
            }
            self._active.pop(rid, None)
            self._emit("CANDIDATE_INVALID", st, bar_time=int(entry_time or 0),
                       diagnostic="CANDIDATE_PERSISTENCE_FAILED")
        return rid

    def evaluate_bar(self, *, symbol: str, bar_time: int, bar_high: float,
                     bar_low: float, bar_close: float,
                     bar_open=None, bar_index: int = 0) -> list:
        bar_time = int(bar_time)
        outcomes: list = []
        for rid, st in list(self._active.items()):
            if st.symbol != symbol:
                continue
            if st.state != CANDIDATE_STATE_ACTIVE or st.terminal:
                continue
            if rid in self._degraded:
                continue
            if bar_time <= int(st.last_evaluated_bar_time):
                outcomes.append({"runtime_id": rid, "status": "DUPLICATE_IGNORED",
                                 "bar_time": bar_time})
                continue
            adapter = self._adapters.get(rid)
            if adapter is None:
                continue
            bar = TreatmentBar(symbol=symbol, bar_time_utc=bar_time,
                               bar_open=bar_open, bar_high=float(bar_high),
                               bar_low=float(bar_low),
                               bar_close=float(bar_close),
                               bar_index=int(bar_index))
            entry_geometry = {
                "entry_price": st.entry_price,
                "stop_loss": st.original_stop_loss,
                "take_profit": st.original_take_profit,
                "risk_distance": st.original_risk_distance,
            }
            try:
                res = adapter.on_bar(
                    entry_geometry=entry_geometry,
                    risk_distance=st.original_risk_distance,
                    direction=st.direction, bar=bar,
                    prior_state=dict(st.treatment_state or {}))
            except Exception as exc:
                outcomes.append({"runtime_id": rid, "status": "NON_EVALUABLE",
                                 "diagnostic": str(exc)})
                continue
            st.treatment_state = dict(res.treatment_state or {})
            st.last_evaluated_bar_time = bar_time
            st.bars_elapsed += 1
            if res.terminal:
                st.terminal = True
                st.state = CANDIDATE_STATE_CLOSED
                st.candidate_exit_reason = str(res.exit_reason or "candidate_terminal")
                px = res.exit_price if res.exit_price is not None else float(bar_close)
                st.candidate_exit_price = float(px)
                st.candidate_exit_time = bar_time
                st.candidate_r = compute_candidate_r(
                    st.direction, st.entry_price,
                    float(st.candidate_exit_price), st.original_risk_distance)
                ok = self._emit("CANDIDATE_CLOSE", st, bar_time=bar_time,
                                diagnostic=str(res.diagnostic or ""))
                self._close_count[rid] = int(self._close_count.get(rid, 0)) + 1
                self._active.pop(rid, None)
                self._closed.add(rid)
                outcomes.append({"runtime_id": rid, "status": "CLOSED",
                                 "bar_time": bar_time})
                if not ok:
                    self._degraded[rid] = "CANDIDATE_PERSISTENCE_FAILED"
                    self._integrity_failures[rid] = {
                        "candidate_id": st.candidate_id,
                        "policy_id": st.policy_id,
                        "reason": "CANDIDATE_PERSISTENCE_FAILED",
                    }
                continue
            ok = self._emit("CANDIDATE_PROGRESS", st, bar_time=bar_time,
                            diagnostic=str(res.diagnostic or ""))
            if not ok:
                self._degraded[rid] = "CANDIDATE_PERSISTENCE_FAILED"
                st.state = CANDIDATE_STATE_INVALID
                self._invalid[rid] = "CANDIDATE_PERSISTENCE_FAILED"
                self._integrity_failures[rid] = {
                    "candidate_id": st.candidate_id,
                    "policy_id": st.policy_id,
                    "reason": "CANDIDATE_PERSISTENCE_FAILED",
                }
                self._active.pop(rid, None)
                self._emit("CANDIDATE_INVALID", st, bar_time=bar_time,
                           diagnostic="CANDIDATE_PERSISTENCE_FAILED")
                outcomes.append({"runtime_id": rid, "status": "DEGRADED",
                                 "bar_time": bar_time})
                continue
            outcomes.append({"runtime_id": rid, "status": "PROGRESSED",
                             "bar_time": bar_time})
        return outcomes

    def _emit(self, event_type: str, st, *,
              bar_time: int, diagnostic: str) -> bool:


        ev = {
            "schema_version": SCHEMA_VERSION,
            "model_version": CANDIDATE_MODEL_VERSION,
            "runtime_version": CANDIDATE_RUNTIME_VERSION,
            "producer": "shadow_candidate_runtime",
            "event_type": event_type,
            "event_id": f"{st.runtime_id}:{event_type}:{int(bar_time or st.entry_time or 0)}",
            "candidate_runtime_id": st.runtime_id,
            "candidate_id": st.candidate_id,
            "policy_id": st.policy_id,
            "treatment_hash": st.treatment_hash,
            "shadow_trade_id": st.shadow_trade_id,
            "canonical_opportunity_id": st.canonical_opportunity_id,
            "trade_horizon": st.trade_horizon,
            "symbol": st.symbol,
            "experiment_id": st.experiment_id,
            "experiment_arm": st.experiment_arm,
            "state": st.to_dict(),
            "watermark": int(st.last_evaluated_bar_time),
            "treatment_state": dict(st.treatment_state or {}),
            "diagnostic": diagnostic,
            "lineage": dict(st.lineage or {}),
            "bar_time_utc": int(bar_time),
        }
        if event_type == "CANDIDATE_CLOSE":
            ev["outcome"] = {
                "exit_reason": st.candidate_exit_reason,
                "exit_price": st.candidate_exit_price,
                "exit_time": st.candidate_exit_time,
                "candidate_r": st.candidate_r,
                "experiment_id": st.experiment_id,
                "experiment_arm": st.experiment_arm,
            }
        try:
            return bool(self._writer.append(
                event=ev, symbol=st.symbol or "UNKNOWN",
                market_time_utc=int(bar_time or st.entry_time or 0)))
        except Exception:
            return False

    def ensure_recovered(self) -> dict:
        """Restore persisted candidate state once registrations are available."""
        if self._recovered:
            return {"restored": 0, "quarantined": 0,
                    "active": len(self._active), "closed": len(self._closed)}
        if self._integrity_failures:
            self._recovered = True
            return {"restored": 0, "quarantined": len(self._integrity_failures),
                    "active": len(self._active), "closed": len(self._closed)}
        return self.recover()

    def recover(self) -> dict:
        events = load_candidate_events(self._writer.base_dir)
        self._active = {}
        self._closed = set()
        self._invalid = {}
        self._close_count = {}
        self._degraded = {}
        self._integrity_failures = {}
        self._canonical_bindings = {}
        restored = 0
        quarantined = 0
        for ev in events:
            et = str(ev.get("event_type", ""))
            state_d = dict(ev.get("state", {}) or {})
            try:
                st = CandidateState.from_dict(state_d)
            except Exception:
                continue
            rid = str(ev.get("candidate_runtime_id") or st.runtime_id)
            canonical_key = (
                str(st.canonical_opportunity_id), str(st.trade_horizon).upper(),
                str(st.candidate_id), str(st.policy_id),
            )
            if ev.get("schema_version") != SCHEMA_VERSION:
                if rid not in self._invalid:
                    quarantined += 1
                self._invalid[rid] = "CANDIDATE_SCHEMA_MISMATCH"
                self._integrity_failures[rid] = {
                    "candidate_id": st.candidate_id,
                    "policy_id": st.policy_id,
                    "reason": "CANDIDATE_SCHEMA_MISMATCH",
                }
                continue
            key = (st.candidate_id, st.policy_id)
            reg = self._registrations.get(key)
            if reg is not None and int(st.entry_time) < int(
                    reg.activation_frontier_epoch_s or 0):
                self._active.pop(rid, None)
                if rid not in self._invalid:
                    quarantined += 1
                self._invalid[rid] = "CANDIDATE_PRE_BIND_EVIDENCE_INELIGIBLE"
                self._integrity_failures[rid] = {
                    "candidate_id": st.candidate_id,
                    "policy_id": st.policy_id,
                    "reason": "CANDIDATE_PRE_BIND_EVIDENCE_INELIGIBLE",
                }
                continue
            if reg is not None and str(reg.treatment_hash) != str(st.treatment_hash):
                self._active.pop(rid, None)
                if rid not in self._invalid:
                    quarantined += 1
                self._invalid[rid] = "CANDIDATE_TREATMENT_HASH_MISMATCH"
                self._integrity_failures[rid] = {
                    "candidate_id": st.candidate_id,
                    "policy_id": st.policy_id,
                    "reason": "CANDIDATE_TREATMENT_HASH_MISMATCH",
                }
                continue
            if et == "CANDIDATE_OPEN":
                if rid in self._closed or rid in self._invalid:
                    continue
                cur = self._active.get(rid)
                if cur is None:
                    self._active[rid] = st
                    restored += 1
                elif int(st.last_evaluated_bar_time) >= int(cur.last_evaluated_bar_time):
                    self._active[rid] = st
            elif et == "CANDIDATE_PROGRESS":
                if rid in self._closed or rid in self._invalid:
                    continue
                cur = self._active.get(rid)
                if cur is None:
                    self._active[rid] = st
                    restored += 1
                elif int(st.last_evaluated_bar_time) >= int(cur.last_evaluated_bar_time):
                    self._active[rid] = st
            elif et == "CANDIDATE_CLOSE":
                self._active.pop(rid, None)
                if rid not in self._closed:
                    self._close_count[rid] = int(self._close_count.get(rid, 0)) + 1
                self._closed.add(rid)
            elif et == "CANDIDATE_INVALID":
                self._active.pop(rid, None)
                if rid not in self._invalid:
                    quarantined += 1
                self._invalid[rid] = str(ev.get("diagnostic", "CANDIDATE_INVALID"))
                self._integrity_failures[rid] = {
                    "candidate_id": st.candidate_id,
                    "policy_id": st.policy_id,
                    "reason": self._invalid[rid],
                }
            if et in {"CANDIDATE_OPEN", "CANDIDATE_PROGRESS", "CANDIDATE_CLOSE"}:
                existing_rid = self._canonical_bindings.get(canonical_key)
                if existing_rid and existing_rid != rid:
                    self._invalid[rid] = "CANDIDATE_CANONICAL_BINDING_CONFLICT"
                    self._integrity_failures[rid] = {
                        "candidate_id": st.candidate_id,
                        "policy_id": st.policy_id,
                        "reason": "CANDIDATE_CANONICAL_BINDING_CONFLICT",
                    }
                    self._active.pop(rid, None)
                    quarantined += 1
                else:
                    self._canonical_bindings[canonical_key] = rid
        for rid, st in list(self._active.items()):
            reg = self._registrations.get((st.candidate_id, st.policy_id))
            if reg is None:
                self._invalid[rid] = "CANDIDATE_NOT_REGISTERED_AT_RECOVERY"
                self._integrity_failures[rid] = {
                    "candidate_id": st.candidate_id,
                    "policy_id": st.policy_id,
                    "reason": "CANDIDATE_NOT_REGISTERED_AT_RECOVERY",
                }
                self._active.pop(rid, None)
                quarantined += 1
                continue
            if str(reg.treatment_hash) != str(st.treatment_hash):
                self._invalid[rid] = "CANDIDATE_TREATMENT_HASH_MISMATCH"
                self._integrity_failures[rid] = {
                    "candidate_id": st.candidate_id,
                    "policy_id": st.policy_id,
                    "reason": "CANDIDATE_TREATMENT_HASH_MISMATCH",
                }
                self._active.pop(rid, None)
                quarantined += 1
                continue
            self._adapters[rid] = reg.adapter
        self._recovered = True
        return {"restored": restored, "quarantined": quarantined,
                "active": len(self._active), "closed": len(self._closed)}

    def snapshot(self, runtime_id: str):
        st = self._active.get(runtime_id)
        if st is None:
            return None
        return dict(st.to_dict())

    def active_ids(self) -> list:
        return list(self._active.keys())

    def quarantined_ids(self) -> dict:
        return dict(self._invalid)

    def degraded_ids(self) -> dict:
        return dict(self._degraded)

    def integrity_failures(self) -> list[dict[str, str]]:
        return [dict(value, runtime_id=rid)
                for rid, value in self._integrity_failures.items()]

    def close_count(self, runtime_id: str) -> int:
        return int(self._close_count.get(runtime_id, 0))

_CANDIDATE_RUNTIME = None


def get_candidate_runtime() -> CandidateRuntime:
    global _CANDIDATE_RUNTIME
    if _CANDIDATE_RUNTIME is None:
        _CANDIDATE_RUNTIME = CandidateRuntime()
        from core.shadow.opt_dp1_002 import register_governed_shadow_candidates
        register_governed_shadow_candidates(_CANDIDATE_RUNTIME)
    return _CANDIDATE_RUNTIME


def reset_candidate_runtime() -> None:
    global _CANDIDATE_RUNTIME
    _CANDIDATE_RUNTIME = None
