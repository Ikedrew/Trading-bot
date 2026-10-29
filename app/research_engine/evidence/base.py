"""
Shared evidence-consumer helpers.

Provides the common report scaffold and lineage-coverage accounting used by all
Step-4 dataset evidence consumers. Consumers stay read-only S3-backed: they
record how many records carry each canonical join key so joins that silently
degrade (e.g. null trade_id on entry attempts) are explicit in the report.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from research_engine.dataset_disposition import (
    ResearchDisposition,
    dataset_disposition,
)


@dataclass(frozen=True)
class EvidenceReport:
    """Skeleton for a dataset evidence report."""

    dataset: str
    record_count: int
    disposition_status: str
    temporal_availability: str
    research_purpose: str
    lineage_coverage: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    guard_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "record_count": self.record_count,
            "disposition_status": self.disposition_status,
            "temporal_availability": self.temporal_availability,
            "research_purpose": self.research_purpose,
            "lineage_coverage": self.lineage_coverage,
            "analysis": self.analysis,
            "guard_notes": self.guard_notes,
        }


def lineage_coverage(
    records: Iterable[dict[str, Any]],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    """Count how many records carry each canonical join key (non-empty value).

    Nested keys use dotted paths (e.g. ``broker_result.deal``). Order of ``keys``
    is the preferred join order — the first key with the highest non-empty share
    is the practical join key.
    """
    recs = list(records)
    total = len(recs)
    out: dict[str, Any] = {
        "total_records": total,
        "key_coverage": {},
        "best_join_key": "",
    }
    best_key, best_count = "", -1
    for key in keys:
        count = 0
        for rec in recs:
            cur: Any = rec
            found = False
            for part in key.split("."):
                if not isinstance(cur, dict):
                    found = False
                    break
                cur = cur.get(part)
                found = True
            if found and cur not in (None, "", 0):
                count += 1
        share = (count / total) if total else 0.0
        out["key_coverage"][key] = {
            "non_empty": count,
            "share": round(share, 4),
        }
        if count > best_count:
            best_count, best_key = count, key
    out["best_join_key"] = best_key
    return out


def disposition_of(dataset: str) -> ResearchDisposition:
    """Look up the dataset's disposition, failing loudly if unclassified."""
    disp = dataset_disposition(dataset)
    if disp is None:
        raise ValueError(
            f"'{dataset}' has no research disposition — register it in "
            f"research_engine.dataset_disposition before consuming it."
        )
    return disp


def counter_summary(values: Iterable[str], top: int = 12) -> dict[str, int]:
    """Top-N Counter dict (most common first), int-coerced for JSON safety."""
    return {
        k: int(v) for k, v in Counter(v for v in values if v).most_common(top)
    }


def numeric_stats(values: Iterable[float]) -> dict[str, float]:
    """Min/max/mean/n-count over non-None numeric values."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"count": 0}
    return {
        "count": len(vals),
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
        "mean": round(sum(vals) / len(vals), 6),
    }