"""
Placebo Controller — Negative control testing for research hypotheses.

Determines whether an observed effect is SPECIFIC to the hypothesised
population or is a GENERAL property of the dataset/period.

Method:
    1. Take the same experimental protocol (e.g., direction inversion)
    2. Apply it to UNRELATED populations (other patterns, other strategies)
    3. If the majority of unrelated populations show the same effect,
       the hypothesis is weakened (it's a dataset property, not pattern-specific)

This is the critical falsification test that caught the TBC/TWS inversion
bias as a general phenomenon rather than a pattern-specific reversal signal.

This module NEVER modifies production V10.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PlaceboResult:
    """Result of running a placebo test on one control population."""
    pattern: str
    n: int
    mean_r: float
    is_positive: bool


@dataclass
class PlaceboTestOutcome:
    """
    Complete outcome of placebo testing across all control populations.
    
    Interpretation:
        placebo_passes = True: The effect is specific to the hypothesis population
        placebo_passes = False: The effect is general (appears in most populations)
    """
    hypothesis_id: str = ""
    positive_placebos: int = 0
    total_placebos: int = 0
    positive_fraction: float = 0.0
    threshold: float = 0.5              # If > threshold are positive, hypothesis is weakened
    placebo_passes: bool = True         # True = effect is specific, False = general
    results: list[PlaceboResult] = field(default_factory=list)
    interpretation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "positive_placebos": self.positive_placebos,
            "total_placebos": self.total_placebos,
            "positive_fraction": round(self.positive_fraction, 3),
            "threshold": self.threshold,
            "placebo_passes": self.placebo_passes,
            "results": [{"pattern": r.pattern, "n": r.n, "mean_r": round(r.mean_r, 4),
                         "positive": r.is_positive} for r in self.results],
            "interpretation": self.interpretation,
        }


def run_placebo_test(
    *,
    hypothesis_id: str,
    experiment_fn: Callable[[list[dict], str], list[float]],
    control_populations: dict[str, list[dict]],
    min_n: int = 20,
    positive_threshold: float = 0.5,
) -> PlaceboTestOutcome:
    """
    Run the same experimental protocol on control populations.
    
    Args:
        hypothesis_id: ID of the hypothesis being tested
        experiment_fn: Function that takes (population, pattern_name) and returns list of R values
        control_populations: dict of pattern_name → list of observation records
        min_n: Minimum sample size for a control to count
        positive_threshold: Fraction above which the placebo test fails
    
    Returns:
        PlaceboTestOutcome with pass/fail determination
    """
    results: list[PlaceboResult] = []

    for pattern, population in control_populations.items():
        if len(population) < min_n:
            continue

        try:
            r_values = experiment_fn(population, pattern)
            if not r_values:
                continue

            mean_r = statistics.mean(r_values)
            results.append(PlaceboResult(
                pattern=pattern,
                n=len(r_values),
                mean_r=mean_r,
                is_positive=mean_r > 0,
            ))
        except Exception:
            continue

    positive_count = sum(1 for r in results if r.is_positive)
    total = len(results)
    fraction = positive_count / total if total > 0 else 0

    passes = fraction <= positive_threshold

    if passes:
        interpretation = (
            f"Placebo PASSES: {positive_count}/{total} control patterns show positive R "
            f"(≤{positive_threshold:.0%} threshold). Effect appears SPECIFIC to hypothesis population."
        )
    else:
        interpretation = (
            f"Placebo FAILS: {positive_count}/{total} control patterns show positive R "
            f"(>{positive_threshold:.0%} threshold). Effect appears GENERAL — not specific to hypothesis."
        )

    return PlaceboTestOutcome(
        hypothesis_id=hypothesis_id,
        positive_placebos=positive_count,
        total_placebos=total,
        positive_fraction=fraction,
        threshold=positive_threshold,
        placebo_passes=passes,
        results=results,
        interpretation=interpretation,
    )
