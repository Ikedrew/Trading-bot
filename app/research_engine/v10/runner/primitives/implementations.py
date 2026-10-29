"""
Analysis Primitive Implementations.

12 reusable analytical primitives for the research question engine.
Each receives a population + parameters and returns AnalysisResult.
None write files or modify state.

Primitives:
    expectancy, distribution, comparison, conditional_expectancy,
    calibration, predictive_power, segmentation, transition,
    execution_quality, degradation, anomaly_analysis, exceptional_analysis
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

from research_engine.v10.runner.primitives.base import AnalysisPrimitive, AnalysisResult


# ═══════════════════════════════════════════════════════════════════════════════
# 1. EXPECTANCY
# ═══════════════════════════════════════════════════════════════════════════════


class ExpectancyPrimitive(AnalysisPrimitive):
    """Computes trade expectancy metrics from R-multiple data."""

    @property
    def name(self) -> str:
        return "expectancy"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        r_field = params.get("r_field", "r_multiple")

        r_values = [r[r_field] for r in population if r.get(r_field) is not None]
        n = len(r_values)

        if n == 0:
            return AnalysisResult(
                analysis_type=self.name, success=True, sample_size=0,
                warnings=["No records with R-multiple data"],
                metrics={"count": 0},
            )

        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r <= 0]
        win_rate = len(wins) / n if n else 0
        mean_r = statistics.mean(r_values)
        median_r = statistics.median(r_values)
        total_r = sum(r_values)
        avg_win = statistics.mean(wins) if wins else 0
        avg_loss = statistics.mean(losses) if losses else 0
        profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf")
        std_r = statistics.stdev(r_values) if n > 1 else 0

        metrics = {
            "count": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "mean_r": round(mean_r, 4),
            "median_r": round(median_r, 4),
            "total_r": round(total_r, 4),
            "avg_win_r": round(avg_win, 4),
            "avg_loss_r": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
            "std_r": round(std_r, 4),
            "expectancy": round(mean_r, 4),
        }

        warnings = []
        if n < 30:
            warnings.append(f"Small sample ({n} trades) — low statistical confidence")

        evidence = []
        if mean_r > 0:
            evidence.append(f"Positive expectancy: {mean_r:+.4f}R per trade")
        elif mean_r < 0:
            evidence.append(f"Negative expectancy: {mean_r:+.4f}R per trade")
        else:
            evidence.append("Expectancy is approximately zero")

        return AnalysisResult(
            analysis_type=self.name, success=True, sample_size=n,
            metrics=metrics, evidence=evidence, warnings=warnings,
            distributions={"r_values": _percentiles(r_values)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DISTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════════


class DistributionPrimitive(AnalysisPrimitive):
    """Analyses the distribution of a numeric field."""

    @property
    def name(self) -> str:
        return "distribution"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        field_name = params.get("field", "r_multiple")

        values = [r[field_name] for r in population if r.get(field_name) is not None]
        n = len(values)

        if n == 0:
            return AnalysisResult(
                analysis_type=self.name, success=True, sample_size=0,
                warnings=[f"No records with field '{field_name}'"],
            )

        metrics = {
            "count": n,
            "mean": round(statistics.mean(values), 4),
            "median": round(statistics.median(values), 4),
            "std": round(statistics.stdev(values), 4) if n > 1 else 0,
            "min": round(min(values), 4),
            "max": round(max(values), 4),
        }

        return AnalysisResult(
            analysis_type=self.name, success=True, sample_size=n,
            metrics=metrics,
            distributions={field_name: _percentiles(values)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════


class ComparisonPrimitive(AnalysisPrimitive):
    """Compares metrics between two or more population groups."""

    @property
    def name(self) -> str:
        return "comparison"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        group_field = params.get("group_field", "regime")
        metric_field = params.get("metric_field", "r_multiple")
        min_per_group = params.get("min_per_group", 3)

        # Stage 1: Filter to records with BOTH fields present
        groups: dict[str, list[float]] = defaultdict(list)
        records_with_both = 0
        records_missing_group = 0
        records_missing_metric = 0

        for r in population:
            group = r.get(group_field)
            val = r.get(metric_field)

            # Coerce group to string for grouping (handles bool, None, etc.)
            if group is None or group == "":
                records_missing_group += 1
                continue
            if val is None:
                records_missing_metric += 1
                continue

            groups[str(group)].append(val)
            records_with_both += 1

        # Stage 2: Analytical sample is records actually used
        analytical_sample = records_with_both

        if analytical_sample == 0:
            return AnalysisResult(
                analysis_type=self.name, success=True,
                sample_size=0,
                warnings=[
                    f"No records contain both '{group_field}' and '{metric_field}'",
                    f"Population: {len(population)}, missing group: {records_missing_group}, missing metric: {records_missing_metric}",
                ],
                metrics={
                    "population_size": len(population),
                    "analytical_sample": 0,
                    "records_missing_group": records_missing_group,
                    "records_missing_metric": records_missing_metric,
                },
            )

        # Stage 3: Classify groups
        groups_discovered = len(groups)
        sufficient_groups = {k: v for k, v in groups.items() if len(v) >= min_per_group}
        insufficient_groups = {k: v for k, v in groups.items() if len(v) < min_per_group}

        if not sufficient_groups:
            return AnalysisResult(
                analysis_type=self.name, success=True,
                sample_size=analytical_sample,
                warnings=[
                    f"All {groups_discovered} groups have fewer than {min_per_group} observations each",
                ],
                metrics={
                    "population_size": len(population),
                    "analytical_sample": analytical_sample,
                    "groups_discovered": groups_discovered,
                    "groups_sufficient": 0,
                    "groups_insufficient": groups_discovered,
                },
                sub_sample_sizes={k: len(v) for k, v in groups.items()},
            )

        # Stage 4: Compute comparison for sufficient groups
        comparisons = {}
        for grp, vals in sorted(sufficient_groups.items()):
            comparisons[grp] = {
                "count": len(vals),
                "mean": round(statistics.mean(vals), 4),
                "median": round(statistics.median(vals), 4),
                "total": round(sum(vals), 4),
                "win_rate": round(len([v for v in vals if v > 0]) / len(vals), 4),
            }

        # Stage 5: Effect size between two largest sufficient groups
        sorted_groups = sorted(sufficient_groups.items(), key=lambda x: -len(x[1]))
        effect = {}
        if len(sorted_groups) >= 2:
            g1_name, g1_vals = sorted_groups[0]
            g2_name, g2_vals = sorted_groups[1]
            diff = statistics.mean(g1_vals) - statistics.mean(g2_vals)
            pooled_std = _pooled_std(g1_vals, g2_vals)
            cohens_d = diff / pooled_std if pooled_std > 0 else 0
            effect = {
                "groups": [g1_name, g2_name],
                "mean_difference": round(diff, 4),
                "cohens_d": round(cohens_d, 4),
            }

        # Stage 6: Top-level metrics for outcome determination
        all_means = [statistics.mean(v) for v in sufficient_groups.values()]
        overall_mean = statistics.mean([v for vals in sufficient_groups.values() for v in vals])
        spread = max(all_means) - min(all_means) if len(all_means) >= 2 else 0

        metrics = {
            "population_size": len(population),
            "analytical_sample": analytical_sample,
            "groups_discovered": groups_discovered,
            "groups_sufficient": len(sufficient_groups),
            "groups_insufficient": len(insufficient_groups),
            "overall_mean": round(overall_mean, 4),
            "group_spread": round(spread, 4),
            "mean_r": round(overall_mean, 4),  # For outcome determination
        }

        evidence = []
        if spread > 0.1:
            evidence.append(f"Groups differ by {spread:.4f}R (spread between best and worst group)")
        else:
            evidence.append(f"Groups are similar (spread: {spread:.4f}R)")

        if insufficient_groups:
            evidence.append(f"{len(insufficient_groups)} groups excluded (< {min_per_group} observations)")

        return AnalysisResult(
            analysis_type=self.name, success=True,
            sample_size=analytical_sample,
            metrics=metrics,
            comparisons=comparisons,
            effect_sizes=effect,
            sub_sample_sizes={k: len(v) for k, v in groups.items()},
            evidence=evidence,
            warnings=[f"Insufficient groups excluded: {list(insufficient_groups.keys())}"] if insufficient_groups else [],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CONDITIONAL EXPECTANCY
# ═══════════════════════════════════════════════════════════════════════════════


class ConditionalExpectancyPrimitive(AnalysisPrimitive):
    """Expectancy conditioned on one or more categorical fields."""

    @property
    def name(self) -> str:
        return "conditional_expectancy"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        condition_fields = params.get("condition_fields", ["regime"])
        r_field = params.get("r_field", "r_multiple")

        segments: dict[str, dict[str, Any]] = {}
        groups: dict[str, list[float]] = defaultdict(list)

        for r in population:
            key_parts = [str(r.get(f, "")) for f in condition_fields]
            key = "|".join(key_parts)
            val = r.get(r_field)
            if val is not None and all(key_parts):
                groups[key].append(val)

        for key, vals in sorted(groups.items()):
            n = len(vals)
            segments[key] = {
                "count": n,
                "mean_r": round(statistics.mean(vals), 4) if vals else 0,
                "win_rate": round(len([v for v in vals if v > 0]) / n, 4) if n else 0,
                "total_r": round(sum(vals), 4),
            }

        return AnalysisResult(
            analysis_type=self.name, success=True,
            sample_size=len(population),
            segments=segments,
            sub_sample_sizes={k: v["count"] for k, v in segments.items()},
            metrics={"conditions": condition_fields, "segment_count": len(segments)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CALIBRATION
# ═══════════════════════════════════════════════════════════════════════════════


class CalibrationPrimitive(AnalysisPrimitive):
    """Checks if predicted probability/score is calibrated to actual outcomes."""

    @property
    def name(self) -> str:
        return "calibration"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        predicted_field = params.get("predicted_field", "p_success")
        outcome_field = params.get("outcome_field", "r_multiple")
        n_buckets = params.get("buckets", 5)

        pairs = [
            (r[predicted_field], r[outcome_field])
            for r in population
            if r.get(predicted_field) is not None and r.get(outcome_field) is not None
        ]
        n = len(pairs)

        if n < 10:
            return AnalysisResult(
                analysis_type=self.name, success=True, sample_size=n,
                warnings=[f"Insufficient calibration data ({n} records)"],
            )

        pairs.sort(key=lambda x: x[0])
        bucket_size = max(1, n // n_buckets)
        buckets = {}

        for i in range(0, n, bucket_size):
            chunk = pairs[i:i + bucket_size]
            if not chunk:
                continue
            predicted_mean = statistics.mean(p for p, _ in chunk)
            actual_win_rate = len([o for _, o in chunk if o > 0]) / len(chunk)
            buckets[f"bucket_{i // bucket_size + 1}"] = {
                "predicted_mean": round(predicted_mean, 4),
                "actual_win_rate": round(actual_win_rate, 4),
                "count": len(chunk),
                "calibration_error": round(abs(predicted_mean - actual_win_rate), 4),
            }

        avg_error = statistics.mean(
            b["calibration_error"] for b in buckets.values()
        ) if buckets else 0

        return AnalysisResult(
            analysis_type=self.name, success=True, sample_size=n,
            metrics={"mean_calibration_error": round(avg_error, 4), "buckets": len(buckets)},
            segments=buckets,
            evidence=[
                f"Mean calibration error: {avg_error:.4f}",
                "Well-calibrated" if avg_error < 0.1 else "Poorly calibrated",
            ],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. PREDICTIVE POWER
# ═══════════════════════════════════════════════════════════════════════════════


class PredictivePowerPrimitive(AnalysisPrimitive):
    """Determines if a variable contains useful predictive information about outcomes."""

    @property
    def name(self) -> str:
        return "predictive_power"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        feature_field = params.get("feature_field", "score")
        outcome_field = params.get("outcome_field", "r_multiple")
        n_buckets = params.get("buckets", 4)

        pairs = [
            (r[feature_field], r[outcome_field])
            for r in population
            if r.get(feature_field) is not None and r.get(outcome_field) is not None
        ]
        n = len(pairs)

        if n < 10:
            return AnalysisResult(
                analysis_type=self.name, success=True, sample_size=n,
                warnings=[f"Insufficient data ({n} records)"],
            )

        pairs.sort(key=lambda x: x[0])
        bucket_size = max(1, n // n_buckets)
        buckets = {}
        bucket_means = []

        for i in range(0, n, bucket_size):
            chunk = pairs[i:i + bucket_size]
            if not chunk:
                continue
            feat_mean = statistics.mean(f for f, _ in chunk)
            outcome_mean = statistics.mean(o for _, o in chunk)
            bucket_means.append(outcome_mean)
            buckets[f"q{i // bucket_size + 1}"] = {
                "feature_mean": round(feat_mean, 4),
                "outcome_mean": round(outcome_mean, 4),
                "count": len(chunk),
            }

        # Monotonicity check (does higher feature → higher outcome?)
        monotonic = all(
            bucket_means[i] <= bucket_means[i + 1]
            for i in range(len(bucket_means) - 1)
        ) if len(bucket_means) > 1 else False

        # Spread between top and bottom bucket
        spread = bucket_means[-1] - bucket_means[0] if len(bucket_means) >= 2 else 0

        return AnalysisResult(
            analysis_type=self.name, success=True, sample_size=n,
            metrics={
                "monotonic": monotonic,
                "top_bottom_spread": round(spread, 4),
                "bucket_count": len(buckets),
            },
            segments=buckets,
            evidence=[
                f"Feature '{feature_field}' {'IS' if monotonic else 'is NOT'} monotonically predictive",
                f"Top-bottom spread: {spread:+.4f}R",
            ],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SEGMENTATION
# ═══════════════════════════════════════════════════════════════════════════════


class SegmentationPrimitive(AnalysisPrimitive):
    """Generic segmentation by one or more categorical dimensions."""

    @property
    def name(self) -> str:
        return "segmentation"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        dimensions = params.get("dimensions", ["symbol"])
        metric_field = params.get("metric_field", "r_multiple")

        groups: dict[str, list[float]] = defaultdict(list)
        for r in population:
            key_parts = [str(r.get(d, "")) for d in dimensions]
            if all(key_parts):
                key = " | ".join(key_parts)
                val = r.get(metric_field)
                if val is not None:
                    groups[key].append(val)

        segments = {}
        for key, vals in sorted(groups.items()):
            segments[key] = {
                "count": len(vals),
                "mean": round(statistics.mean(vals), 4) if vals else 0,
                "total": round(sum(vals), 4),
                "win_rate": round(len([v for v in vals if v > 0]) / len(vals), 4) if vals else 0,
            }

        return AnalysisResult(
            analysis_type=self.name, success=True,
            sample_size=len(population),
            segments=segments,
            metrics={"dimensions": dimensions, "segment_count": len(segments)},
            sub_sample_sizes={k: v["count"] for k, v in segments.items()},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. TRANSITION
# ═══════════════════════════════════════════════════════════════════════════════


class TransitionPrimitive(AnalysisPrimitive):
    """Analyses state transitions or temporal changes."""

    @property
    def name(self) -> str:
        return "transition"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        state_field = params.get("state_field", "regime")
        time_field = params.get("time_field", "entry_time")
        metric_field = params.get("metric_field", "r_multiple")

        # Sort by time
        timed = [(r.get(time_field, 0), r) for r in population if r.get(time_field)]
        timed.sort(key=lambda x: x[0])

        if len(timed) < 2:
            return AnalysisResult(
                analysis_type=self.name, success=True, sample_size=len(timed),
                warnings=["Insufficient temporal data for transition analysis"],
            )

        # Detect transitions
        transitions: dict[str, int] = Counter()
        prev_state = timed[0][1].get(state_field, "")
        for _, r in timed[1:]:
            curr_state = r.get(state_field, "")
            if curr_state and prev_state and curr_state != prev_state:
                transitions[f"{prev_state}->{curr_state}"] += 1
            if curr_state:
                prev_state = curr_state

        # Period comparison (first half vs second half)
        mid = len(timed) // 2
        first_half = [r.get(metric_field) for _, r in timed[:mid] if r.get(metric_field) is not None]
        second_half = [r.get(metric_field) for _, r in timed[mid:] if r.get(metric_field) is not None]

        comparisons = {}
        if first_half and second_half:
            comparisons = {
                "first_half": {"mean": round(statistics.mean(first_half), 4), "count": len(first_half)},
                "second_half": {"mean": round(statistics.mean(second_half), 4), "count": len(second_half)},
                "change": round(statistics.mean(second_half) - statistics.mean(first_half), 4),
            }

        return AnalysisResult(
            analysis_type=self.name, success=True,
            sample_size=len(timed),
            metrics={"transitions_detected": sum(transitions.values()), "unique_transitions": len(transitions)},
            segments=dict(transitions),
            comparisons=comparisons,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 9. EXECUTION QUALITY
# ═══════════════════════════════════════════════════════════════════════════════


class ExecutionQualityPrimitive(AnalysisPrimitive):
    """Analyses execution quality (slippage, duration, fill behaviour)."""

    @property
    def name(self) -> str:
        return "execution_quality"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}

        durations = [r["duration_seconds"] for r in population if r.get("duration_seconds") is not None]
        exit_reasons = Counter(r.get("exit_reason", "") for r in population if r.get("exit_reason"))

        metrics: dict[str, Any] = {"count": len(population)}

        if durations:
            metrics["mean_duration_s"] = round(statistics.mean(durations), 1)
            metrics["median_duration_s"] = round(statistics.median(durations), 1)

        if exit_reasons:
            metrics["exit_reason_distribution"] = dict(exit_reasons.most_common())
            total_exits = sum(exit_reasons.values())
            metrics["sl_hit_rate"] = round(
                exit_reasons.get("STOP_LOSS", 0) / total_exits, 4
            ) if total_exits else 0
            metrics["tp_hit_rate"] = round(
                exit_reasons.get("TAKE_PROFIT", 0) / total_exits, 4
            ) if total_exits else 0

        return AnalysisResult(
            analysis_type=self.name, success=True,
            sample_size=len(population),
            metrics=metrics,
            distributions={"exit_reasons": dict(exit_reasons)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 10. DEGRADATION
# ═══════════════════════════════════════════════════════════════════════════════


class DegradationPrimitive(AnalysisPrimitive):
    """Compares performance across time periods to detect degradation."""

    @property
    def name(self) -> str:
        return "degradation"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        time_field = params.get("time_field", "entry_time")
        metric_field = params.get("metric_field", "r_multiple")
        n_periods = params.get("periods", 3)

        timed = [
            (r[time_field], r.get(metric_field))
            for r in population
            if r.get(time_field) is not None and r.get(metric_field) is not None
        ]
        timed.sort()
        n = len(timed)

        if n < n_periods * 3:
            return AnalysisResult(
                analysis_type=self.name, success=True, sample_size=n,
                warnings=[f"Insufficient data for {n_periods}-period degradation analysis"],
            )

        period_size = n // n_periods
        periods = {}
        period_means = []

        for i in range(n_periods):
            start = i * period_size
            end = start + period_size if i < n_periods - 1 else n
            chunk = timed[start:end]
            vals = [v for _, v in chunk]
            mean_val = statistics.mean(vals) if vals else 0
            period_means.append(mean_val)
            periods[f"period_{i + 1}"] = {
                "count": len(vals),
                "mean": round(mean_val, 4),
                "start_time": chunk[0][0] if chunk else None,
                "end_time": chunk[-1][0] if chunk else None,
            }

        # Detect degradation trend
        degrading = all(
            period_means[i] >= period_means[i + 1]
            for i in range(len(period_means) - 1)
        ) if len(period_means) > 1 else False

        improving = all(
            period_means[i] <= period_means[i + 1]
            for i in range(len(period_means) - 1)
        ) if len(period_means) > 1 else False

        trend = "DEGRADING" if degrading else "IMPROVING" if improving else "STABLE"

        return AnalysisResult(
            analysis_type=self.name, success=True, sample_size=n,
            metrics={"trend": trend, "periods": n_periods},
            segments=periods,
            evidence=[f"Performance trend: {trend} across {n_periods} periods"],
            comparisons={
                "first_period_mean": round(period_means[0], 4) if period_means else 0,
                "last_period_mean": round(period_means[-1], 4) if period_means else 0,
            },
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 11. ANOMALY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════


class AnomalyAnalysisPrimitive(AnalysisPrimitive):
    """Separates and analyses anomalous records within a population."""

    @property
    def name(self) -> str:
        return "anomaly_analysis"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        anomaly_field = params.get("anomaly_field", "anomaly")
        metric_field = params.get("metric_field", "r_multiple")

        normal = [r for r in population if not r.get(anomaly_field, False)]
        anomalous = [r for r in population if r.get(anomaly_field, False)]

        metrics: dict[str, Any] = {
            "total": len(population),
            "normal_count": len(normal),
            "anomaly_count": len(anomalous),
            "anomaly_rate": round(len(anomalous) / len(population), 4) if population else 0,
        }

        # Compare metrics between normal and anomalous
        normal_vals = [r.get(metric_field) for r in normal if r.get(metric_field) is not None]
        anomaly_vals = [r.get(metric_field) for r in anomalous if r.get(metric_field) is not None]

        if normal_vals:
            metrics["normal_mean"] = round(statistics.mean(normal_vals), 4)
        if anomaly_vals:
            metrics["anomaly_mean"] = round(statistics.mean(anomaly_vals), 4)

        comparisons = {}
        if normal_vals and anomaly_vals:
            comparisons = {
                "normal_vs_anomaly": {
                    "normal_mean": round(statistics.mean(normal_vals), 4),
                    "anomaly_mean": round(statistics.mean(anomaly_vals), 4),
                    "difference": round(
                        statistics.mean(anomaly_vals) - statistics.mean(normal_vals), 4
                    ),
                }
            }

        return AnalysisResult(
            analysis_type=self.name, success=True,
            sample_size=len(population),
            metrics=metrics,
            comparisons=comparisons,
            sub_sample_sizes={"normal": len(normal), "anomalous": len(anomalous)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 12. EXCEPTIONAL ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════


class ExceptionalAnalysisPrimitive(AnalysisPrimitive):
    """Identifies and analyses exceptional observations (extreme outcomes)."""

    @property
    def name(self) -> str:
        return "exceptional_analysis"

    def analyse(
        self, population: list[dict[str, Any]], parameters: dict[str, Any] | None = None
    ) -> AnalysisResult:
        params = parameters or {}
        metric_field = params.get("metric_field", "r_multiple")
        threshold_high = params.get("threshold_high", 2.0)
        threshold_low = params.get("threshold_low", -2.0)

        values = [(r, r.get(metric_field)) for r in population if r.get(metric_field) is not None]

        normal = [(r, v) for r, v in values if threshold_low <= v <= threshold_high]
        exceptional_high = [(r, v) for r, v in values if v > threshold_high]
        exceptional_low = [(r, v) for r, v in values if v < threshold_low]

        metrics = {
            "total": len(values),
            "normal_count": len(normal),
            "exceptional_high_count": len(exceptional_high),
            "exceptional_low_count": len(exceptional_low),
            "exceptional_rate": round(
                (len(exceptional_high) + len(exceptional_low)) / len(values), 4
            ) if values else 0,
        }

        if exceptional_high:
            metrics["exceptional_high_mean"] = round(
                statistics.mean(v for _, v in exceptional_high), 4
            )
        if exceptional_low:
            metrics["exceptional_low_mean"] = round(
                statistics.mean(v for _, v in exceptional_low), 4
            )

        return AnalysisResult(
            analysis_type=self.name, success=True,
            sample_size=len(values),
            metrics=metrics,
            sub_sample_sizes={
                "normal": len(normal),
                "exceptional_high": len(exceptional_high),
                "exceptional_low": len(exceptional_low),
            },
            evidence=[
                f"{len(exceptional_high)} records above {threshold_high}R",
                f"{len(exceptional_low)} records below {threshold_low}R",
            ],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _percentiles(values: list[float]) -> dict[str, float]:
    """Compute standard percentiles for a list of values."""
    if not values:
        return {}
    s = sorted(values)
    n = len(s)
    return {
        "p5": round(s[max(0, int(n * 0.05))], 4),
        "p25": round(s[max(0, int(n * 0.25))], 4),
        "p50": round(s[max(0, int(n * 0.50))], 4),
        "p75": round(s[max(0, int(n * 0.75))], 4),
        "p95": round(s[min(n - 1, int(n * 0.95))], 4),
    }


def _pooled_std(a: list[float], b: list[float]) -> float:
    """Compute pooled standard deviation for two groups."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 1.0  # Avoid division by zero
    var_a = statistics.variance(a)
    var_b = statistics.variance(b)
    return ((var_a * (na - 1) + var_b * (nb - 1)) / (na + nb - 2)) ** 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════


def build_default_registry() -> "AnalysisRegistry":
    """Create and populate the default registry with all 12 primitives."""
    from research_engine.v10.runner.primitives.base import AnalysisRegistry

    registry = AnalysisRegistry()
    registry.register(ExpectancyPrimitive())
    registry.register(DistributionPrimitive())
    registry.register(ComparisonPrimitive())
    registry.register(ConditionalExpectancyPrimitive())
    registry.register(CalibrationPrimitive())
    registry.register(PredictivePowerPrimitive())
    registry.register(SegmentationPrimitive())
    registry.register(TransitionPrimitive())
    registry.register(ExecutionQualityPrimitive())
    registry.register(DegradationPrimitive())
    registry.register(AnomalyAnalysisPrimitive())
    registry.register(ExceptionalAnalysisPrimitive())
    return registry
