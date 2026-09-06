"""
V10 RESEARCH ENGINE — FIRST AUTHORITATIVE BASELINE RUN

Executes all GREEN/READY + AMBER questions against current evidence.
Produces the definitive V10 research baseline.

Does NOT modify V10, questions, or data.
"""
import sys
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, ".")

from research_engine.v10.universes.legacy_question_bank import (
    QUESTION_BANK, QUESTION_BANK_BY_ID, get_question,
)
from research_engine.v10.universes.models import (
    Universe, Population, QuestionStatus, AnalysisType,
)
from research_engine.v10.runner.primitive_mapping import (
    QUESTION_PARAMETERS, build_full_mapping,
)
from research_engine.v10.runner.primitives.implementations import build_default_registry
from research_engine.v10.runner.question_runner import QuestionRunner, RunContext
from research_engine.v10.universes import (
    ExecutionUniverseBuilder, DecisionUniverseBuilder,
    MarketUniverseBuilder, StrategyUniverseBuilder,
    RiskUniverseBuilder, OutcomeUniverseBuilder,
)
from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
from research_engine.v10.universes.outcome_enrichment import OutcomeEnrichment


def main():
    run_start = time.time()
    run_id = f"baseline_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    
    # ═══════════════════════════════════════════════════════════════
    # 1. FREEZE RESEARCH CONTRACT
    # ═══════════════════════════════════════════════════════════════
    
    # Question bank hash
    bank_content = json.dumps([q.to_dict() for q in QUESTION_BANK], sort_keys=True, default=str)
    bank_hash = hashlib.sha256(bank_content.encode()).hexdigest()[:16]
    
    executable = [q for q in QUESTION_BANK if q.status in (QuestionStatus.READY, QuestionStatus.PARTIAL)]
    blocked = [q for q in QUESTION_BANK if q.status == QuestionStatus.BLOCKED]
    
    print(f"═══ V10 RESEARCH ENGINE — BASELINE RUN ═══")
    print(f"Run ID: {run_id}")
    print(f"Question bank hash: {bank_hash}")
    print(f"Total questions: {len(QUESTION_BANK)}")
    print(f"Executable (READY+PARTIAL): {len(executable)}")
    print(f"Blocked: {len(blocked)}")
    print()
    
    # ═══════════════════════════════════════════════════════════════
    # 2. BUILD UNIVERSES
    # ═══════════════════════════════════════════════════════════════
    print("Building universes...")
    
    builders = {}
    for UClass, utype in [
        (ExecutionUniverseBuilder, Universe.EXECUTION),
        (DecisionUniverseBuilder, Universe.DECISION),
        (MarketUniverseBuilder, Universe.MARKET),
        (StrategyUniverseBuilder, Universe.STRATEGY),
        (RiskUniverseBuilder, Universe.RISK),
    ]:
        b = UClass()
        b.build()
        builders[utype] = b
        print(f"  {utype.value}: {b.metadata.record_count} records")
    
    # Outcome enrichment
    exe = builders[Universe.EXECUTION]
    enrichment = OutcomeEnrichment(exe)
    enrich_results = enrichment.enrich_all(builders)
    
    # Outcome universe
    outcome = OutcomeUniverseBuilder(execution_builder=exe)
    outcome.build()
    builders[Universe.OUTCOME] = outcome
    print(f"  OUTCOME: {outcome.metadata.record_count} records")
    
    # Shadow universe
    shadow = ShadowOutcomeUniverseBuilder()
    shadow.build()
    builders[Universe.SHADOW_OUTCOME] = shadow
    print(f"  SHADOW_OUTCOME: {shadow.metadata.record_count} records")
    print()
    
    # ═══════════════════════════════════════════════════════════════
    # 3. EXECUTE ALL QUESTIONS
    # ═══════════════════════════════════════════════════════════════
    print("Executing research questions...")
    
    registry = build_default_registry()
    mapping = build_full_mapping(QUESTION_BANK)
    runner = QuestionRunner(registry, mapping)
    ctx = RunContext(run_id=run_id)
    
    results = []
    findings_data = []
    
    for q in executable:
        # Resolve population
        primary_u = q.required_universes[0]
        builder = builders.get(primary_u)
        if not builder:
            results.append({
                "question_id": q.question_id,
                "status": "ERROR",
                "error": f"No builder for universe {primary_u.value}",
            })
            continue
        
        pop = builder.get_population(q.required_populations[0]) if q.required_populations else builder.records
        
        # Execute
        try:
            result = runner.run_question(q, pop, ctx)
        except Exception as exc:
            results.append({
                "question_id": q.question_id,
                "status": "ERROR",
                "error": str(exc)[:200],
            })
            continue
        
        # Record result
        finding = result.finding
        entry = {
            "question_id": q.question_id,
            "title": q.title,
            "status": "COMPLETE" if result.success else "FAILED",
            "population_size": len(pop),
            "evidence_source": finding.evidence_source if finding else "UNKNOWN",
            "outcome": finding.outcome if finding else "",
            "confidence": finding.confidence if finding else "",
            "sample_sizes": finding.sample_sizes if finding else {},
            "primary_metrics": {},
            "warnings": [],
            "error": result.error if not result.success else "",
        }
        
        if finding:
            # Extract key metrics (limit size)
            metrics = finding.primary_metrics or {}
            entry["primary_metrics"] = {k: v for k, v in list(metrics.items())[:15]}
            entry["warnings"] = (finding.limitations or [])[:5]
            findings_data.append(finding.to_dict())
        
        results.append(entry)
        
        # Progress indicator
        status_icon = "+" if entry["status"] == "COMPLETE" and entry["confidence"] != "INSUFFICIENT" else "~" if entry["status"] == "COMPLETE" else "!"
        print(f"  {status_icon} {q.question_id:<10} {entry['outcome'][:20]:<20} conf={entry['confidence']:<12} pop={entry['population_size']}")
    
    run_duration = time.time() - run_start
    
    # ═══════════════════════════════════════════════════════════════
    # 4. PRODUCE REPORTS
    # ═══════════════════════════════════════════════════════════════
    print(f"\nRun complete in {run_duration:.1f}s")
    
    # Save results
    output_dir = Path("reports/research/baseline")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Manifest
    manifest = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_seconds": round(run_duration, 1),
        "question_bank_hash": bank_hash,
        "total_questions": len(QUESTION_BANK),
        "executed": len(executable),
        "blocked": len(blocked),
        "universes": {u.value: b.metadata.record_count for u, b in builders.items()},
        "enrichment": {k: v.to_dict() for k, v in enrich_results.items()} if isinstance(enrich_results, dict) else {},
    }
    (output_dir / "baseline_run_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    
    # Results JSON
    (output_dir / "baseline_run_results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    
    # ═══════════════════════════════════════════════════════════════
    # 5. GENERATE MARKDOWN REPORT
    # ═══════════════════════════════════════════════════════════════
    md = []
    md.append("# V10 RESEARCH ENGINE — FIRST AUTHORITATIVE BASELINE")
    md.append(f"\n**Run ID:** {run_id}")
    md.append(f"**Timestamp:** {manifest['timestamp']}")
    md.append(f"**Duration:** {run_duration:.1f}s")
    md.append(f"**Question Bank Hash:** {bank_hash}")
    md.append("")
    md.append("## UNIVERSE SIZES")
    md.append("")
    for u, b in builders.items():
        md.append(f"- {u.value}: {b.metadata.record_count} records")
    md.append("")
    
    # Categorise results
    complete = [r for r in results if r["status"] == "COMPLETE" and r["confidence"] not in ("INSUFFICIENT", "")]
    inconclusive = [r for r in results if r["status"] == "COMPLETE" and r["confidence"] == "INSUFFICIENT"]
    errors = [r for r in results if r["status"] in ("ERROR", "FAILED")]
    
    md.append("## EXECUTION SUMMARY")
    md.append(f"\n- **COMPLETE (meaningful finding):** {len(complete)}")
    md.append(f"- **INCONCLUSIVE (insufficient):** {len(inconclusive)}")
    md.append(f"- **ERROR:** {len(errors)}")
    md.append(f"- **BLOCKED (not executed):** {len(blocked)}")
    md.append("")
    
    # ─── FINDINGS BY DOMAIN ───────────────────────────────────────
    domains = defaultdict(list)
    for r in results:
        qid = r["question_id"]
        if qid.startswith("E-") or qid.startswith("E0"):
            domains["EXECUTION"].append(r)
        elif qid.startswith("D-"):
            domains["DECISION"].append(r)
        elif qid.startswith("M-"):
            domains["MARKET"].append(r)
        elif qid.startswith("S-"):
            domains["STRATEGY"].append(r)
        elif qid.startswith("SD-"):
            domains["SHADOW"].append(r)
        else:
            domains["CROSS-DOMAIN"].append(r)
    
    for domain_name in ["EXECUTION", "DECISION", "MARKET", "STRATEGY", "SHADOW", "CROSS-DOMAIN"]:
        domain_results = domains.get(domain_name, [])
        if not domain_results:
            continue
        md.append(f"## {domain_name} FINDINGS")
        md.append("")
        for r in domain_results:
            qid = r["question_id"]
            title = r.get("title", "")
            outcome = r.get("outcome", "—")
            conf = r.get("confidence", "—")
            pop = r.get("population_size", 0)
            ev = r.get("evidence_source", "")
            metrics = r.get("primary_metrics", {})
            warnings = r.get("warnings", [])
            
            md.append(f"### {qid} — {title}")
            md.append(f"- **Outcome:** {outcome}")
            md.append(f"- **Confidence:** {conf}")
            md.append(f"- **Evidence:** {ev}")
            md.append(f"- **Population:** {pop}")
            
            # Key metrics
            if metrics:
                key_metrics = []
                for k, v in list(metrics.items())[:8]:
                    if isinstance(v, float):
                        key_metrics.append(f"{k}={v:+.4f}" if abs(v) < 100 else f"{k}={v:.1f}")
                    elif isinstance(v, (int, bool)):
                        key_metrics.append(f"{k}={v}")
                if key_metrics:
                    md.append(f"- **Metrics:** {', '.join(key_metrics)}")
            
            if warnings:
                md.append(f"- **Warnings:** {'; '.join(warnings[:3])}")
            md.append("")
    
    # ─── CONVERGENT EVIDENCE ──────────────────────────────────────
    md.append("## CONVERGENT EVIDENCE")
    md.append("")
    
    # Find regime-related convergence
    regime_findings = []
    for r in results:
        if r["status"] == "COMPLETE" and r.get("primary_metrics"):
            metrics = r["primary_metrics"]
            if "dimensions" in metrics and "regime" in str(metrics.get("dimensions", "")):
                regime_findings.append(r)
            if "group_spread" in metrics:
                regime_findings.append(r)
    
    if regime_findings:
        md.append("### Regime-Related Convergence")
        for r in regime_findings[:5]:
            md.append(f"- {r['question_id']}: outcome={r['outcome']}, spread={r.get('primary_metrics',{}).get('group_spread','?')}")
        md.append("")
    
    # ─── OPTIMISATION LEADS ───────────────────────────────────────
    md.append("## POTENTIAL OPTIMISATION LEADS")
    md.append("")
    md.append("*(Observations only — NOT recommendations to implement)*")
    md.append("")
    
    for r in complete:
        outcome = r.get("outcome", "")
        conf = r.get("confidence", "")
        if outcome in ("NEGATIVE", "NOT_PREDICTIVE") and conf in ("HIGH", "MEDIUM"):
            md.append(f"- **{r['question_id']}** ({r.get('title','')}): {outcome} [{conf}] — may indicate area for investigation")
        elif outcome == "POSITIVE" and conf == "HIGH":
            md.append(f"- **{r['question_id']}** ({r.get('title','')}): {outcome} [{conf}] — confirms current approach")
    md.append("")
    
    # ─── WHAT WE NOW KNOW ─────────────────────────────────────────
    md.append("## WHAT DO WE NOW KNOW ABOUT V10?")
    md.append("")
    
    # Find system expectancy
    e001 = next((r for r in results if r["question_id"] == "E-001"), None)
    sd001 = next((r for r in results if r["question_id"] == "SD-001"), None)
    sd004 = next((r for r in results if r["question_id"] == "SD-004"), None)
    
    if e001 and e001.get("primary_metrics"):
        mean_r = e001["primary_metrics"].get("mean_r") or e001["primary_metrics"].get("expectancy")
        if mean_r is not None:
            md.append(f"### Realised Performance")
            md.append(f"- System expectancy (E-001): **{mean_r:+.4f}R** per trade")
            wr = e001["primary_metrics"].get("win_rate")
            if wr: md.append(f"- Win rate: {wr:.0%}" if wr < 1 else f"- Win rate: {wr:.1%}")
            count = e001["primary_metrics"].get("count", 0)
            md.append(f"- Sample: {count} trades")
            md.append("")
    
    if sd001 and sd001.get("primary_metrics"):
        shadow_r = sd001["primary_metrics"].get("mean_r") or sd001["primary_metrics"].get("expectancy")
        if shadow_r is not None:
            md.append(f"### Counterfactual Opportunity Pool")
            md.append(f"- Shadow expectancy (SD-001): **{shadow_r:+.4f}R** per opportunity")
            sw = sd001["primary_metrics"].get("win_rate")
            if sw: md.append(f"- Shadow win rate: {sw:.0%}" if sw < 1 else f"- Shadow win rate: {sw:.1%}")
            sc = sd001["primary_metrics"].get("count", 0)
            md.append(f"- Opportunities observed: {sc}")
            md.append("")
    
    if sd004 and sd004.get("primary_metrics"):
        md.append(f"### Rejection Stage Analysis")
        md.append(f"- SD-004 outcome: {sd004.get('outcome', '?')}")
        segment_count = sd004["primary_metrics"].get("segment_count", 0)
        md.append(f"- Rejection stages analysed: {segment_count}")
        md.append("")
    
    md.append("---")
    md.append("")
    md.append("*This is the authoritative V10 research baseline. No changes have been made to V10.*")
    md.append("*Next step: Review findings → Design experiments → Validate candidates → Human approval.*")
    
    report_text = "\n".join(md)
    (output_dir / "baseline_run_report.md").write_text(report_text, encoding="utf-8")
    
    print(f"\nReports saved to: {output_dir}/")
    print(f"  baseline_run_manifest.json")
    print(f"  baseline_run_results.json")
    print(f"  baseline_run_report.md")
    print(f"\nComplete: {len(complete)} findings, {len(inconclusive)} inconclusive, {len(errors)} errors")


if __name__ == "__main__":
    main()
