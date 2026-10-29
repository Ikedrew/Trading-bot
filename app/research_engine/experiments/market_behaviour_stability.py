"""Canonical HD11 L4 market-behaviour stability experiment."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import statistics
from typing import Any, Mapping

from research_engine.control_plane.evidence_provenance import (
    attest_current_subset, build_evidence_provenance, evidence_digest,
    select_current_evidence,
)
from research_engine.control_plane.shadow_timestamp_normalization import (
    CANDLE_NORMALIZATION_VERSION, NORMALIZATION_CONTRACT_VERSION,
    TIMESTAMP_SEMANTICS,
)
from research_engine.experiments.experiment_base import (
    build_fingerprint_from_provenance, build_report,
)
from research_engine.experiments.pattern_degradation import (
    _canonical_json, _current_open_entry_time, _finite_number, _identity,
    _lifecycle_key, _outcome, _resolve_entry_time, _timestamp_sources,
)
from research_engine.registry.learning_adaptation_adjudication import (
    ALPHA, HD11_VERSION, L4_HOLM_ORDER, L4_MIN, L4_REGIME_CELLS,
)

REPORT_FILENAME = "l4_market_behaviour_stability.json"
PRIMARY_OUTCOME = "PRIMARY_HORIZON_SIMULATION"
TV_THRESHOLD = 0.10
ADVERSE_R_THRESHOLD = -0.25


def _base_core(record: dict[str, Any]) -> tuple[str, str, float] | None:
    identity = _identity(record)
    opportunity_id = str(identity.get("canonical_opportunity_id", "") or "").strip()
    symbol = str(identity.get("symbol", "") or record.get("symbol", "") or "").strip()
    r_value = _finite_number(_outcome(record).get("pnl_r_multiple"))
    if not opportunity_id or not symbol or r_value is None:
        return None
    return opportunity_id, symbol, r_value


def _select_shadow_observations(
    current: list[dict[str, Any]],
    opens: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    closes: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    candle_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int], list[str], list[dict[str, Any]]]:
    exclusions: Counter[str] = Counter()
    grouped: dict[str, list[tuple[dict[str, Any], int]]] = defaultdict(list)
    timestamp_provenance: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for record in current:
        if str(_identity(record).get("shadow_type", "") or "") != PRIMARY_OUTCOME:
            exclusions["non_primary_horizon_simulation"] += 1
            continue
        core = _base_core(record)
        if core is None:
            exclusions["missing_or_invalid_shadow_field"] += 1
            continue
        entry_time, provenance = _resolve_entry_time(record, opens, closes, candle_events)
        timestamp_provenance.append(provenance)
        if entry_time is None:
            exclusions["unresolved_governed_entry_time"] += 1
            continue
        source_open = opens.get(_lifecycle_key(record), [])
        if len(source_open) != 1 or str(source_open[0].get("symbol", "") or "").strip() != core[1]:
            exclusions["open_opportunity_symbol_conflict"] += 1
            conflicts.append(core[0])
            continue
        grouped[core[0]].append((record, entry_time))

    observations: list[dict[str, Any]] = []
    entry_times: dict[str, int] = {}
    for opportunity_id in sorted(grouped):
        candidates = grouped[opportunity_id]
        cores = {
            (base[0], base[1], entry_time, base[2])
            for record, entry_time in candidates
            if (base := _base_core(record)) is not None
        }
        if len(cores) != 1:
            conflicts.append(opportunity_id)
            exclusions["conflicting_primary_outcome"] += len(candidates)
            continue
        observations.append(min((item[0] for item in candidates), key=_canonical_json))
        entry_times[opportunity_id] = candidates[0][1]
        if len(candidates) > 1:
            exclusions["collapsed_duplicate_or_account_fanout"] += len(candidates) - 1
    return observations, entry_times, dict(exclusions), conflicts, sorted(
        timestamp_provenance, key=lambda item: (item.get("identity", []), item.get("reason", ""))
    )


def _context_core(record: dict[str, Any]) -> tuple[str, int, str] | None:
    symbol = str(record.get("symbol", "") or "").strip()
    entity_id = str(record.get("entity_id", "") or "").strip()
    stamp = _finite_number(record.get("bar_time"))
    regime = str(record.get("regime", "") or "").strip().upper()
    if not symbol or not entity_id or stamp is None or int(stamp) != stamp or stamp <= 0 or regime not in L4_REGIME_CELLS:
        return None
    return symbol, int(stamp), regime


def _join_context(
    observations: list[dict[str, Any]], entry_times: Mapping[str, int],
    contexts: list[dict[str, Any]], exclusions: Counter[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    by_symbol_time: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in contexts:
        core = _context_core(record)
        if core is None:
            exclusions["missing_or_invalid_market_context_field"] += 1
            continue
        by_symbol_time[core[0]][core[1]].append(record)

    joined: list[dict[str, Any]] = []
    used: dict[str, dict[str, Any]] = {}
    conflicts: list[str] = []
    for record in observations:
        opportunity_id, symbol, r_value = _base_core(record)  # type: ignore[misc]
        entry_time = entry_times[opportunity_id]
        preceding = [stamp for stamp in by_symbol_time.get(symbol, {}) if stamp <= entry_time]
        if not preceding:
            exclusions["no_preceding_same_symbol_context"] += 1
            continue
        selected_time = max(preceding)
        candidates = by_symbol_time[symbol][selected_time]
        distinct_records = {_canonical_json(item) for item in candidates}
        if len(distinct_records) != 1:
            conflicts.append(opportunity_id)
            exclusions["conflicting_latest_context_tie"] += len(candidates)
            continue
        representative = min(candidates, key=_canonical_json)
        selected_core = _context_core(representative)
        assert selected_core is not None
        used[_canonical_json(representative)] = representative
        joined.append({
            "record": record, "canonical_opportunity_id": opportunity_id,
            "symbol": symbol, "entry_time": entry_time,
            "context_time": selected_time, "regime": selected_core[2],
            "r_multiple": r_value,
        })
        if len(candidates) > 1:
            exclusions["collapsed_identical_context_duplicates"] += len(candidates) - 1
    return joined, list(used.values()), sorted(conflicts)


def _beta_fraction(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (1e-300 if abs(d) < 1e-300 else d)
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d; d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c; c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d; h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d; d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c; c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c; h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0: return 0.0
    if x >= 1.0: return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


def _welch_p(t_value: float, degrees_freedom: float) -> float:
    x = degrees_freedom / (degrees_freedom + t_value * t_value)
    return min(1.0, max(0.0, _regularized_beta(x, degrees_freedom / 2.0, 0.5)))


def _t_critical_95(degrees_freedom: float) -> float:
    low, high = 0.0, 2.0
    while _welch_p(high, degrees_freedom) > ALPHA:
        high *= 2.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if _welch_p(middle, degrees_freedom) > ALPHA: low = middle
        else: high = middle
    return (low + high) / 2.0


def _welch(early: list[float], late: list[float]) -> dict[str, Any] | None:
    if len(early) < 2 or len(late) < 2:
        return None
    early_mean, late_mean = statistics.fmean(early), statistics.fmean(late)
    a, b = statistics.variance(early) / len(early), statistics.variance(late) / len(late)
    se2 = a + b
    denominator = a * a / (len(early) - 1) + b * b / (len(late) - 1)
    if not math.isfinite(se2) or se2 <= 0.0 or not math.isfinite(denominator) or denominator <= 0.0:
        return None
    se = math.sqrt(se2)
    degrees = se2 * se2 / denominator
    delta = late_mean - early_mean
    p_value = _welch_p(abs(delta / se), degrees)
    critical = _t_critical_95(degrees)
    return {
        "early_n": len(early), "late_n": len(late),
        "early_mean_r": round(early_mean, 12), "late_mean_r": round(late_mean, 12),
        "late_minus_early_mean_r": round(delta, 12),
        "standard_error": round(se, 12), "satterthwaite_df": round(degrees, 12),
        "interval_95": {"lower_r": round(delta - critical * se, 12), "upper_r": round(delta + critical * se, 12)},
        "raw_two_sided_p_value": round(p_value, 12),
    }


def _holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    position = {name: index for index, name in enumerate(L4_HOLM_ORDER)}
    ordered = sorted(p_values.items(), key=lambda item: (item[1], position[item[0]]))
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (name, p_value) in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - index) * p_value))
        adjusted[name] = running
    return adjusted


def _inference_config() -> dict[str, Any]:
    return {
        "alpha": ALPHA, "holm_order": list(L4_HOLM_ORDER),
        "mix_test": "Pearson 2x3 chi-square homogeneity test, df=2, upper-tail p-value",
        "r_test": "two-sided independent-window Welch t test with Satterthwaite df and 95% CI",
        "mix_materiality_tv_gte": TV_THRESHOLD,
        "adverse_r_materiality_delta_lte": ADVERSE_R_THRESHOLD,
    }


def _mixed_epoch(selection: Any) -> bool:
    counts = selection.component["epoch_counts"]
    return bool(counts["CURRENT"] and sum(counts[key] for key in ("TRANSITIONAL", "LEGACY", "INCOMPATIBLE")))


def validate_l4_report(report: dict[str, Any]) -> tuple[bool, str]:
    if report.get("question_id") != "L4": return False, "L4 report has wrong canonical identity"
    provenance = report.get("provenance")
    if not isinstance(provenance, dict): return False, "L4 report lacks governed provenance"
    if provenance.get("scientific_owner") != "L4" or provenance.get("report_identity") != REPORT_FILENAME or provenance.get("contract_version") != HD11_VERSION:
        return False, "L4 ownership or contract identity is invalid"
    if provenance.get("inference_config") != _inference_config() or provenance.get("multiplicity_family") != list(L4_HOLM_ORDER):
        return False, "L4 inference or four-test family is invalid"
    evidence_identity = provenance.get("evidence_identity", {})
    if evidence_identity.get("correlation_fallback") is not False or evidence_identity.get("runtime_reconstruction") is not False:
        return False, "L4 forbidden evidence fallback is declared"
    overall = report.get("overall")
    if not isinstance(overall, dict): return False, "L4 overall result is missing"
    chronology = overall.get("chronology", {})
    if chronology.get("order") != "(governed OPEN entry_time, canonical_opportunity_id)" or chronology.get("windows") != "deterministic non-overlapping early/late halves; odd-N midpoint excluded":
        return False, "L4 chronology contract is invalid"
    if overall.get("regime_cells") != list(L4_REGIME_CELLS): return False, "L4 regime cells are invalid"
    if overall.get("holm_family") != list(L4_HOLM_ORDER): return False, "L4 endpoint family is invalid"
    status, result = report.get("status"), overall.get("final_result")
    if status == "COMPLETE":
        if result not in {"STABLE", "MATERIAL_INSTABILITY"}: return False, "L4 COMPLETE result is invalid"
        if report.get("recommendation") != result: return False, "L4 COMPLETE recommendation is invalid"
        endpoints = overall.get("endpoints", {})
        mix, regimes = endpoints.get("regime_mix_shift"), endpoints.get("regime_r_shift")
        if not isinstance(mix, dict) or not isinstance(regimes, dict) or set(regimes) != set(L4_REGIME_CELLS): return False, "L4 COMPLETE endpoints are incomplete"
        raw = {"regime_mix_shift": mix.get("raw_p_value")}
        raw.update({f"{regime}_R_shift": regimes[regime].get("raw_two_sided_p_value") for regime in L4_REGIME_CELLS})
        if not all(isinstance(value, (int, float)) for value in raw.values()): return False, "L4 raw p-values are invalid"
        expected = _holm_adjust(raw)  # type: ignore[arg-type]
        actual = {"regime_mix_shift": mix.get("holm_adjusted_p_value")}
        actual.update({f"{regime}_R_shift": regimes[regime].get("holm_adjusted_p_value") for regime in L4_REGIME_CELLS})
        if any(not isinstance(actual[name], (int, float)) or abs(float(actual[name]) - expected[name]) > 1e-9 for name in L4_HOLM_ORDER): return False, "L4 Holm adjustment is invalid"
        if not isinstance(mix.get("tv_distance"), (int, float)) or not 0.0 <= float(mix["tv_distance"]) <= 1.0:
            return False, "L4 mix effect is invalid"
        mix_significant = expected["regime_mix_shift"] <= ALPHA
        mix_material = float(mix["tv_distance"]) >= TV_THRESHOLD
        if mix.get("holm_significant") != mix_significant or mix.get("material") != mix_material or mix.get("threatening") != (mix_significant and mix_material):
            return False, "L4 mix classification is inconsistent"
        threats = [bool(mix.get("threatening"))]
        for cell in L4_REGIME_CELLS:
            endpoint = regimes[cell]
            delta = endpoint.get("late_minus_early_mean_r")
            if not isinstance(delta, (int, float)):
                return False, "L4 regime-R estimate is invalid"
            name = f"{cell}_R_shift"
            significant = expected[name] <= ALPHA
            material = float(delta) <= ADVERSE_R_THRESHOLD
            if endpoint.get("holm_significant") != significant or endpoint.get("material") != material or endpoint.get("threatening") != (significant and material):
                return False, "L4 regime-R classification is inconsistent"
            threats.append(bool(endpoint.get("threatening")))
        if (result == "MATERIAL_INSTABILITY") != any(threats): return False, "L4 final result contradicts endpoints"
        if chronology.get("early_n", 0) < L4_MIN["per_window"] or chronology.get("late_n", 0) < L4_MIN["per_window"]: return False, "L4 COMPLETE window minimum is unmet"
        if any(regimes[cell].get("early_n", 0) < L4_MIN["per_cell_window"] or regimes[cell].get("late_n", 0) < L4_MIN["per_cell_window"] for cell in L4_REGIME_CELLS): return False, "L4 COMPLETE cell minimum is unmet"
    if bool(overall.get("structural_failures")) != (status == "BLOCKED"): return False, "L4 structural failure state is inconsistent"
    return True, "L4 HD11 report contract is valid"


def run_l4(
    shadow_trades: list[dict[str, Any]] | None = None, *,
    market_context_records: list[dict[str, Any]] | None = None,
    shadow_runtime_events: list[dict[str, Any]] | None = None,
    candle_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate frozen L4 from CURRENT persisted shadow and market-context evidence."""
    if shadow_trades is None:
        from research_engine.data_access.s3_source import get_default_source
        from research_engine.data_access.shadow_runtime_ingestion import load_shadow_runtime_events, reconstruct_completed_shadow_trades
        source_events = list(shadow_runtime_events) if shadow_runtime_events is not None else load_shadow_runtime_events()
        raw_shadow = reconstruct_completed_shadow_trades(source_events)
        historical_needed = any(event.get("event_type") == "OPEN" and _current_open_entry_time(event) is None for event in source_events if isinstance(event, dict))
        source_candles = list(candle_events) if candle_events is not None else (get_default_source().read_dataset("events") if historical_needed else [])
    else:
        raw_shadow = list(shadow_trades)
        source_events = list(shadow_runtime_events or [])
        source_candles = list(candle_events or [])
    if market_context_records is None:
        from research_engine.data_access.s3_source import get_default_source
        raw_context = get_default_source().read_dataset("market_context")
    else:
        raw_context = list(market_context_records)

    shadow_selection = select_current_evidence("shadow_trades", raw_shadow)
    context_selection = select_current_evidence("market_context", raw_context)
    opens, closes = _timestamp_sources(source_events)
    observations, entry_times, initial_exclusions, outcome_conflicts, timestamp_provenance = _select_shadow_observations(
        shadow_selection.records_for_analysis(), opens, closes, source_candles,
    )
    exclusions: Counter[str] = Counter(initial_exclusions)
    joined, used_context, context_conflicts = _join_context(
        observations, entry_times, context_selection.records_for_analysis(), exclusions,
    )
    joined.sort(key=lambda item: (item["entry_time"], item["canonical_opportunity_id"]))
    half = len(joined) // 2
    midpoint = joined[half] if len(joined) % 2 else None
    early, late = joined[:half], joined[half + 1:] if midpoint else joined[half:]
    if midpoint: exclusions["deterministic_midpoint"] += 1

    structural_failures: list[str] = []
    if outcome_conflicts: structural_failures.append("conflicting primary evidence for canonical opportunity")
    if context_conflicts: structural_failures.append("conflicting latest-time market context")
    if exclusions.get("unresolved_governed_entry_time"): structural_failures.append("unresolved governed shadow OPEN entry-time authority")
    if _mixed_epoch(shadow_selection) or _mixed_epoch(context_selection): structural_failures.append("mixed evidence epochs")
    if shadow_selection.component.get("state") != "CURRENT" or context_selection.component.get("state") != "CURRENT": structural_failures.append("missing or invalid CURRENT evidence authority")

    early_values: dict[str, list[float]] = defaultdict(list)
    late_values: dict[str, list[float]] = defaultdict(list)
    for item in early: early_values[item["regime"]].append(item["r_multiple"])
    for item in late: late_values[item["regime"]].append(item["r_multiple"])
    counts_ok = len(early) >= L4_MIN["per_window"] and len(late) >= L4_MIN["per_window"] and all(
        len(early_values[cell]) >= L4_MIN["per_cell_window"] and len(late_values[cell]) >= L4_MIN["per_cell_window"] for cell in L4_REGIME_CELLS
    )
    endpoints: dict[str, Any] = {"regime_mix_shift": None, "regime_r_shift": {}}
    estimable = False
    final_result = None
    if counts_ok:
        early_counts = [len(early_values[cell]) for cell in L4_REGIME_CELLS]
        late_counts = [len(late_values[cell]) for cell in L4_REGIME_CELLS]
        early_shares = [value / len(early) for value in early_counts]
        late_shares = [value / len(late) for value in late_counts]
        tv = 0.5 * sum(abs(a - b) for a, b in zip(early_shares, late_shares))
        columns = [a + b for a, b in zip(early_counts, late_counts)]
        total = len(early) + len(late)
        chi_square = 0.0
        for row_count, row_total in ((early_counts, len(early)), (late_counts, len(late))):
            for observed, column in zip(row_count, columns):
                expected = row_total * column / total
                chi_square += (observed - expected) ** 2 / expected
        mix_p = math.exp(-chi_square / 2.0)
        mix = {
            "early_counts": dict(zip(L4_REGIME_CELLS, early_counts)), "late_counts": dict(zip(L4_REGIME_CELLS, late_counts)),
            "early_shares": dict(zip(L4_REGIME_CELLS, [round(x, 12) for x in early_shares])), "late_shares": dict(zip(L4_REGIME_CELLS, [round(x, 12) for x in late_shares])),
            "tv_distance": round(tv, 12), "chi_square": round(chi_square, 12), "degrees_freedom": 2, "raw_p_value": round(mix_p, 12),
        }
        endpoints["regime_mix_shift"] = mix
        regime_results = {cell: _welch(early_values[cell], late_values[cell]) for cell in L4_REGIME_CELLS}
        endpoints["regime_r_shift"] = regime_results
        estimable = all(value is not None for value in regime_results.values())
        if estimable:
            raw_p = {"regime_mix_shift": mix["raw_p_value"]}
            raw_p.update({f"{cell}_R_shift": regime_results[cell]["raw_two_sided_p_value"] for cell in L4_REGIME_CELLS})  # type: ignore[index]
            adjusted = _holm_adjust(raw_p)
            mix["holm_adjusted_p_value"] = round(adjusted["regime_mix_shift"], 12)
            mix["holm_significant"] = adjusted["regime_mix_shift"] <= ALPHA
            mix["material"] = tv >= TV_THRESHOLD
            mix["threatening"] = mix["holm_significant"] and mix["material"]
            threats = [mix["threatening"]]
            for cell in L4_REGIME_CELLS:
                result = regime_results[cell]; assert result is not None
                name = f"{cell}_R_shift"
                result["holm_adjusted_p_value"] = round(adjusted[name], 12)
                result["holm_significant"] = adjusted[name] <= ALPHA
                result["material"] = result["late_minus_early_mean_r"] <= ADVERSE_R_THRESHOLD
                result["threatening"] = result["holm_significant"] and result["material"]
                threats.append(result["threatening"])
            final_result = "MATERIAL_INSTABILITY" if any(threats) else "STABLE"

    if structural_failures:
        status, recommendation, finding = "BLOCKED", "EVIDENCE_CONTRACT_FAILURE", "L4 evidence contract failed closed"
    elif not counts_ok or not estimable:
        status, recommendation, finding = "WAITING_DATA", "WAIT_FOR_EVIDENCE", "Insufficient or inestimable valid CURRENT evidence for L4"
    else:
        status, recommendation = "COMPLETE", final_result
        finding = "Threatening market-behaviour instability detected" if final_result == "MATERIAL_INSTABILITY" else "No reliable material market-behaviour instability detected"

    selected_shadow = [item["record"] for item in joined]
    shadow_attestation = attest_current_subset("shadow_trades", raw_shadow, selected_shadow)
    context_attestation = attest_current_subset("market_context", raw_context, used_context)
    evidence_provenance = build_evidence_provenance(shadow_attestation, context_attestation)
    fingerprint = build_fingerprint_from_provenance(evidence_provenance, validation_score="BLOCKED" if structural_failures else "CURRENT")
    chronology = {
        "authority": "governed normalized shadow OPEN entry_time UTC epoch seconds",
        "order": "(governed OPEN entry_time, canonical_opportunity_id)",
        "windows": "deterministic non-overlapping early/late halves; odd-N midpoint excluded",
        "early_n": len(early), "late_n": len(late), "midpoint_excluded": int(midpoint is not None),
        "midpoint_identity": midpoint["canonical_opportunity_id"] if midpoint else None,
    }
    overall = {
        "finding": finding, "final_result": final_result,
        "unit_of_analysis": "one canonical opportunity", "population": "CURRENT primary-horizon shadow outcomes",
        "distinct_canonical_opportunities": len(joined), "chronology": chronology,
        "join": {"authority": "same-symbol unique latest-preceding market_context_v1 at or before governed OPEN entry_time", "cardinality": "many opportunities to one context", "contexts_used": len(used_context)},
        "regime_cells": list(L4_REGIME_CELLS), "endpoints": endpoints,
        "holm_family": list(L4_HOLM_ORDER),
        "requirements": {"minimum_per_window": L4_MIN["per_window"], "minimum_per_regime_per_window": L4_MIN["per_cell_window"], "all_four_endpoints_estimable": True},
        "exclusions": dict(sorted(exclusions.items())), "structural_failures": structural_failures,
        "conflicting_primary_opportunity_ids": sorted(outcome_conflicts), "conflicting_context_opportunity_ids": context_conflicts,
    }
    return build_report(
        question_id="L4", status=status, overall=overall,
        confidence="HIGH" if status == "COMPLETE" else "INSUFFICIENT_DATA",
        dataset={"source": "shadow_trades_v1 + market_context_v1", "sample_size": len(joined), "shadow_records_loaded": len(raw_shadow), "context_records_loaded": len(raw_context)},
        fingerprint=fingerprint, recommendation=recommendation,
        assumptions=["Windows and joins are frozen independently of outcomes.", "Account fanout cannot enlarge N.", "L4 has no production-mutation authority."],
        warnings=["Shadow outcomes are simulated research evidence, not realised live P&L."],
        provenance={
            "experiment_module": __name__, "registry_id": "L4", "scientific_owner": "L4", "contract_version": HD11_VERSION, "report_identity": REPORT_FILENAME,
            "evidence_identity": {"shadow_schema": "shadow_trades_v1", "market_context_schema": "market_context_v1", "opportunity_key": "identity.canonical_opportunity_id", "context_join": "same-symbol latest preceding bar_time <= OPEN entry_time", "correlation_fallback": False, "runtime_reconstruction": False},
            "timestamp_authority": {"current_semantics": TIMESTAMP_SEMANTICS, "current_normalization_version": CANDLE_NORMALIZATION_VERSION, "historical_normalization_contract": NORMALIZATION_CONTRACT_VERSION, "records": timestamp_provenance, "digest": evidence_digest(timestamp_provenance)},
            "inference_config": _inference_config(), "multiplicity_family": list(L4_HOLM_ORDER), "production_mutation_authority": False,
        },
    )
