"""
V10 Research Segmentation Engine.

Generates reusable research views from the research universe by filtering
across multiple dimensions: instrument, session, regime, volatility,
confidence, and score.

Does NOT modify source data. Creates filtered research populations.

Usage:
    from research_engine.v10.segmentation_engine import ResearchSegmenter

    seg = ResearchSegmenter()

    # Single dimension
    london_trades = seg.filter(session="LONDON")
    us500_trades = seg.filter(instrument="US500")

    # Multi-dimensional
    trades = seg.filter(
        instrument="US500",
        session="NEW_YORK",
        regime="TRENDING",
        confidence="HIGH",
    )

    # Build all segment files
    seg.build_all_segments()

CLI:
    python -m research_engine.v10.segmentation_engine
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now

logger = logging.getLogger(__name__)

_UNIVERSE_FILE = "data/research/research_universe.jsonl"
_SEGMENTS_DIR = "data/research/segments"
_REPORTS_DIR = "reports/research"

# ═══════════════════════════════════════════════════════════════
# THRESHOLDS
# ═══════════════════════════════════════════════════════════════

_CONFIDENCE_HIGH = 0.6
_CONFIDENCE_LOW = 0.4

_SCORE_HIGH = 0.7
_SCORE_MEDIUM_LOW = 0.5


# ═══════════════════════════════════════════════════════════════
# SEGMENTER CLASS
# ═══════════════════════════════════════════════════════════════

class ResearchSegmenter:
    """
    Multi-dimensional research population filter.

    Loads the research universe and provides filtered views
    across instrument, session, regime, volatility, confidence, and score.
    """

    def __init__(self, universe_file: str | None = None):
        self._universe_file = Path(universe_file or _UNIVERSE_FILE)
        self._events: list[dict] | None = None

    @property
    def events(self) -> list[dict]:
        if self._events is None:
            # S3 is authoritative; local override ignored.
            from research_engine.data_access.s3_source import get_default_source
            self._events = get_default_source().read_artifact("research_universe")
        return self._events

    def filter(
        self,
        instrument: str | None = None,
        session: str | None = None,
        regime: str | None = None,
        volatility: str | None = None,
        confidence: str | None = None,
        score_bucket: str | None = None,
    ) -> list[dict]:
        """
        Filter research universe by one or more dimensions.

        Args:
            instrument: Symbol name (e.g., "EURUSD", "US500") or asset class ("FX", "INDEX", "COMMODITY")
            session: "LONDON", "NEW_YORK", "ASIAN", "LONDON_NY_OVERLAP"
            regime: "TRENDING", "RANGING", "TRANSITIONAL"
            volatility: "HIGH", "LOW", "NEUTRAL"
            confidence: "HIGH", "LOW"
            score_bucket: "HIGH", "MEDIUM", "LOW"

        Returns:
            Filtered list of research universe events.
        """
        result = self.events

        if instrument:
            result = _filter_instrument(result, instrument.upper())
        if session:
            result = _filter_session(result, session.upper())
        if regime:
            result = _filter_regime(result, regime.upper())
        if volatility:
            result = _filter_volatility(result, volatility.upper())
        if confidence:
            result = _filter_confidence(result, confidence.upper())
        if score_bucket:
            result = _filter_score(result, score_bucket.upper())

        return result

    def build_all_segments(
        self,
        segments_dir: str | None = None,
        reports_dir: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate all segment files and metadata report.

        Creates:
            data/research/segments/instruments/{SYMBOL}.jsonl
            data/research/segments/sessions/{SESSION}.jsonl
            data/research/segments/regimes/{REGIME}.jsonl
            data/research/segments/volatility/{VOL}.jsonl
            data/research/segments/confidence/{CONF}.jsonl

        Returns:
            Segmentation summary dict.
        """
        out = Path(segments_dir or _SEGMENTS_DIR)
        rep = Path(reports_dir or _REPORTS_DIR)
        all_events = self.events

        if not all_events:
            return {"error": "No research events loaded"}

        counts: dict[str, dict[str, int]] = {
            "instruments": {},
            "sessions": {},
            "regimes": {},
            "volatility": {},
            "confidence": {},
            "score": {},
        }

        # ─── INSTRUMENTS ──────────────────────────────────────
        inst_dir = out / "instruments"
        inst_dir.mkdir(parents=True, exist_ok=True)
        symbols = sorted(set(e["execution"]["symbol"] for e in all_events))
        for sym in symbols:
            filtered = self.filter(instrument=sym)
            _write_jsonl(inst_dir / f"{sym}.jsonl", filtered)
            counts["instruments"][sym] = len(filtered)

        # Asset class groups
        for cls in ["FX", "INDEX", "COMMODITY"]:
            filtered = self.filter(instrument=cls)
            if filtered:
                _write_jsonl(inst_dir / f"{cls}.jsonl", filtered)
                counts["instruments"][cls] = len(filtered)

        # ─── SESSIONS ─────────────────────────────────────────
        sess_dir = out / "sessions"
        sess_dir.mkdir(parents=True, exist_ok=True)
        for session_name in ["LONDON", "NEW_YORK", "ASIAN", "LONDON_NY_OVERLAP"]:
            filtered = self.filter(session=session_name)
            if filtered:
                _write_jsonl(sess_dir / f"{session_name}.jsonl", filtered)
            counts["sessions"][session_name] = len(filtered)

        # ─── REGIMES ──────────────────────────────────────────
        reg_dir = out / "regimes"
        reg_dir.mkdir(parents=True, exist_ok=True)
        for regime_name in ["TRENDING", "RANGING", "TRANSITIONAL"]:
            filtered = self.filter(regime=regime_name)
            if filtered:
                _write_jsonl(reg_dir / f"{regime_name}.jsonl", filtered)
            counts["regimes"][regime_name] = len(filtered)

        # ─── VOLATILITY ───────────────────────────────────────
        vol_dir = out / "volatility"
        vol_dir.mkdir(parents=True, exist_ok=True)
        for vol_name in ["HIGH", "LOW", "NEUTRAL"]:
            filtered = self.filter(volatility=vol_name)
            if filtered:
                _write_jsonl(vol_dir / f"{vol_name}_VOLATILITY.jsonl", filtered)
            counts["volatility"][vol_name] = len(filtered)

        # ─── CONFIDENCE ───────────────────────────────────────
        conf_dir = out / "confidence"
        conf_dir.mkdir(parents=True, exist_ok=True)
        for conf_name in ["HIGH", "LOW"]:
            filtered = self.filter(confidence=conf_name)
            if filtered:
                _write_jsonl(conf_dir / f"{conf_name}_CONFIDENCE.jsonl", filtered)
            counts["confidence"][conf_name] = len(filtered)

        # ─── SCORE BUCKETS ────────────────────────────────────
        score_dir = out / "score"
        score_dir.mkdir(parents=True, exist_ok=True)
        for bucket in ["HIGH", "MEDIUM", "LOW"]:
            filtered = self.filter(score_bucket=bucket)
            if filtered:
                _write_jsonl(score_dir / f"{bucket}_SCORE.jsonl", filtered)
            counts["score"][bucket] = len(filtered)

        # ─── REPORT ──────────────────────────────────────────
        report = {
            "generated_utc": timestamp_now(),
            "total_events": len(all_events),
            "segments": counts,
            "symbols": symbols,
            "output_dir": str(out),
        }

        rep.mkdir(parents=True, exist_ok=True)
        (rep / "segmentation_engine_report.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        (rep / "segmentation_engine_report.md").write_text(
            _build_markdown(report), encoding="utf-8"
        )

        logger.info(f"[SEGMENTATION_ENGINE] Built segments for {len(all_events)} events across {len(symbols)} symbols")
        return report


# ═══════════════════════════════════════════════════════════════
# FILTER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

_FX_SYMBOLS = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
               "EURJPY", "GBPJPY", "EURGBP", "AUDCAD", "AUDNZD", "NZDCAD"}
_INDEX_SYMBOLS = {"US500", "NAS100", "US30", "GER40", "UK100", "JPN225"}
_COMMODITY_SYMBOLS = {"XAUUSD", "XAGUSD", "USOIL", "UKOIL"}


def _filter_instrument(events: list[dict], instrument: str) -> list[dict]:
    """Filter by instrument or asset class."""
    if instrument == "FX":
        return [e for e in events if e["execution"]["symbol"] in _FX_SYMBOLS]
    elif instrument == "INDEX":
        return [e for e in events if e["execution"]["symbol"] in _INDEX_SYMBOLS]
    elif instrument == "COMMODITY":
        return [e for e in events if e["execution"]["symbol"] in _COMMODITY_SYMBOLS]
    else:
        return [e for e in events if e["execution"]["symbol"] == instrument]


def _filter_session(events: list[dict], session: str) -> list[dict]:
    """Filter by trading session."""
    return [e for e in events if e["market"].get("session", "").upper() == session]


def _filter_regime(events: list[dict], regime: str) -> list[dict]:
    """Filter by market regime."""
    return [e for e in events if e["market"].get("regime", "").upper() == regime]


def _filter_volatility(events: list[dict], volatility: str) -> list[dict]:
    """Filter by volatility state."""
    return [e for e in events if e["market"].get("volatility", "").upper() == volatility]


def _filter_confidence(events: list[dict], confidence: str) -> list[dict]:
    """Filter by confidence bucket."""
    if confidence == "HIGH":
        return [e for e in events if (e["decision"].get("confidence") or 0) >= _CONFIDENCE_HIGH]
    elif confidence == "LOW":
        return [e for e in events if (e["decision"].get("confidence") or 0) < _CONFIDENCE_LOW]
    return events


def _filter_score(events: list[dict], bucket: str) -> list[dict]:
    """Filter by score bucket."""
    if bucket == "HIGH":
        return [e for e in events if (e["decision"].get("score") or 0) >= _SCORE_HIGH]
    elif bucket == "MEDIUM":
        return [e for e in events
                if _SCORE_MEDIUM_LOW <= (e["decision"].get("score") or 0) < _SCORE_HIGH]
    elif bucket == "LOW":
        return [e for e in events if (e["decision"].get("score") or 0) < _SCORE_MEDIUM_LOW]
    return events


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, default=str) for e in events]
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _build_markdown(report: dict) -> str:
    md = []
    md.append("# V10 Research Segmentation Engine Report")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Total events: {report['total_events']}")
    md.append("")

    segments = report["segments"]

    md.append("## Instruments")
    md.append("")
    md.append("| Segment | Count |")
    md.append("|---|---|")
    for k, v in sorted(segments["instruments"].items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")

    md.append("")
    md.append("## Sessions")
    md.append("")
    md.append("| Session | Count |")
    md.append("|---|---|")
    for k, v in sorted(segments["sessions"].items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")

    md.append("")
    md.append("## Regimes")
    md.append("")
    md.append("| Regime | Count |")
    md.append("|---|---|")
    for k, v in sorted(segments["regimes"].items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")

    md.append("")
    md.append("## Volatility")
    md.append("")
    md.append("| State | Count |")
    md.append("|---|---|")
    for k, v in sorted(segments["volatility"].items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")

    md.append("")
    md.append("## Confidence")
    md.append("")
    md.append("| Bucket | Count |")
    md.append("|---|---|")
    for k, v in sorted(segments["confidence"].items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")

    md.append("")
    md.append("## Score Buckets")
    md.append("")
    md.append("| Bucket | Count |")
    md.append("|---|---|")
    for k, v in sorted(segments["score"].items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")

    md.append("")
    md.append("---")
    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    print("=" * 56)
    print("  V10 RESEARCH SEGMENTATION ENGINE")
    print("=" * 56)

    seg = ResearchSegmenter()
    result = seg.build_all_segments()

    if "error" in result:
        print(f"\n  ERROR: {result['error']}")
        sys.exit(1)

    print(f"\n  Total events: {result['total_events']}")
    print(f"  Symbols: {result['symbols']}")

    for dim, counts in result["segments"].items():
        print(f"\n  {dim}:")
        for name, count in sorted(counts.items(), key=lambda x: -x[1]):
            if count > 0:
                print(f"    {name:20s}: {count}")

    print(f"\n  Segments: {result['output_dir']}")
    print(f"  Report: reports/research/segmentation_engine_report.*")
    print("=" * 56)
