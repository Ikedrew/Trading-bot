"""
Cockpit HTML Generator.

Produces a self-contained HTML dashboard from CockpitData.
No external dependencies. Single file output.

Usage:
    from research_engine.v10.cockpit.generator import generate_cockpit
    generate_cockpit()  # → reports/research/cockpit.html
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_engine.v10.cockpit.aggregator import CockpitData, CockpitDataAggregator


def generate_cockpit(
    output_path: Path | str | None = None,
    data: CockpitData | None = None,
) -> Path:
    """
    Generate the research cockpit HTML.

    Args:
        output_path: Where to write the HTML. Defaults to reports/research/cockpit.html.
        data: Pre-aggregated data. If None, aggregates from disk.

    Returns:
        Path to the generated HTML file.
    """
    if data is None:
        data = CockpitDataAggregator().aggregate()

    output = Path(output_path) if output_path else Path("reports/research/cockpit.html")
    output.parent.mkdir(parents=True, exist_ok=True)

    html = _render(data)
    output.write_text(html, encoding="utf-8")
    return output


def _render(data: CockpitData) -> str:
    """Render full HTML cockpit."""
    questions_json = json.dumps(
        [_q_to_dict(q) for q in data.all_questions],
        default=str,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Research Cockpit</title>
<style>
{_CSS}
</style>
</head>
<body>
<div id="app">
<header>
<h1>Research Cockpit</h1>
<div class="meta">Engine {data.engine_version} | Last run: {data.last_run_timestamp or 'never'} | Duration: {data.last_run_duration:.1f}s</div>
</header>

<nav>
<button class="nav-btn active" onclick="showSection('overview')">Overview</button>
<button class="nav-btn" onclick="showSection('universes')">Universes</button>
<button class="nav-btn" onclick="showSection('questions')">Questions</button>
<button class="nav-btn" onclick="showSection('discovery')">Discovery</button>
<button class="nav-btn" onclick="showSection('candidates')">Candidates</button>
<button class="nav-btn" onclick="showSection('shadow_reality')">Shadow↔Reality</button>
<button class="nav-btn" onclick="showSection('runs')">Run History</button>
<button class="nav-btn" onclick="showSection('health')">Health</button>
<button class="nav-btn" onclick="showSection('prop_readiness')">Prop Readiness</button>
<button class="nav-btn" onclick="showSection('development')">Development</button>
</nav>

<div class="search-bar">
<input type="text" id="search" placeholder="Search questions, findings, gaps..." oninput="filterQuestions()">
</div>

<!-- OVERVIEW -->
<section id="overview" class="section active">
<h2>Research Overview</h2>
<div class="cards">
<div class="card complete"><div class="num">{data.complete}</div><div class="lbl">Complete</div></div>
<div class="card inconclusive"><div class="num">{data.inconclusive}</div><div class="lbl">Inconclusive</div></div>
<div class="card blocked"><div class="num">{data.blocked}</div><div class="lbl">Blocked</div></div>
<div class="card error"><div class="num">{data.error}</div><div class="lbl">Error</div></div>
<div class="card notrun"><div class="num">{data.not_run}</div><div class="lbl">Not Run</div></div>
</div>
<div class="cards">
<div class="card"><div class="num">{data.total_questions}</div><div class="lbl">Total Questions</div></div>
<div class="card"><div class="num">{data.total_gaps}</div><div class="lbl">Research Gaps</div></div>
<div class="card"><div class="num">{data.candidates_total}</div><div class="lbl">Candidates</div></div>
<div class="card"><div class="num">{len(data.run_history)}</div><div class="lbl">Runs</div></div>
</div>
<h3>Research Cycle Engine</h3>
<div class="cards">
<div class="card"><div class="num">{data.cycle_total}</div><div class="lbl">Research Cycles</div></div>
<div class="card"><div class="num">{data.triggers_eligible}</div><div class="lbl">Triggers Eligible</div></div>
<div class="card"><div class="num">{data.hypotheses_total}</div><div class="lbl">Hypotheses</div></div>
<div class="card"><div class="num">{data.sr_matched}</div><div class="lbl">Shadow↔Real Pairs</div></div>
</div>
<p class="meta">Last cycle: {data.cycle_last_timestamp[:19] if data.cycle_last_timestamp else 'never'} | Status: {data.cycle_last_status} | Triggers: {data.cycle_last_triggers} | Investigated: {data.cycle_last_investigated}</p>
{_render_universes(data)}
</section>

<!-- UNIVERSES -->
<section id="universes" class="section">
<h2>Research Universes</h2>
{_render_universes_section(data)}
</section>

<!-- QUESTIONS -->
<section id="questions" class="section">
<h2>Question Registry</h2>
<div class="filter-row">
<button onclick="filterByStatus('all')">All</button>
<button onclick="filterByStatus('COMPLETE')">Complete</button>
<button onclick="filterByStatus('INCONCLUSIVE')">Inconclusive</button>
<button onclick="filterByStatus('BLOCKED')">Blocked</button>
</div>
<table id="questions-table">
<thead><tr><th>ID</th><th>Title</th><th>Universes</th><th>Status</th><th>Outcome</th><th>Confidence</th><th>Sample</th></tr></thead>
<tbody>
{_render_question_rows(data.all_questions)}
</tbody>
</table>
</section>

<!-- DISCOVERY -->
<section id="discovery" class="section">
<h2>Autonomous Discovery</h2>
{_render_discovery(data)}
</section>

<!-- CANDIDATES -->
<section id="candidates" class="section">
<h2>Optimisation Candidates</h2>
{_render_candidates(data)}
</section>

<!-- SHADOW ↔ REALITY -->
<section id="shadow_reality" class="section">
<h2>Shadow ↔ Reality</h2>
{_render_shadow_reality(data)}
</section>

<!-- RUN HISTORY -->
<section id="runs" class="section">
<h2>Run History</h2>
<table>
<thead><tr><th>Run ID</th><th>Timestamp</th><th>Requested</th><th>Executed</th><th>Blocked</th><th>Inconclusive</th><th>Duration</th></tr></thead>
<tbody>
{_render_run_rows(data.run_history)}
</tbody>
</table>
</section>

<!-- HEALTH -->
<section id="health" class="section">
<h2>Universe &amp; Population Health</h2>
{_render_health(data)}
<h3>Correlation Health</h3>
{_render_correlation(data)}
</section>

<!-- PROP READINESS -->
<section id="prop_readiness" class="section">
<h2>Prop-Firm Readiness Gate</h2>
{_render_prop_readiness(data)}
</section>

<!-- DEVELOPMENT -->
<section id="development" class="section">
<h2>Question Development</h2>
<div class="cards">
<div class="card"><div class="num">{data.active_questions}</div><div class="lbl">Active Questions</div></div>
<div class="card"><div class="num">{data.total_gaps}</div><div class="lbl">Research Gaps</div></div>
<div class="card"><div class="num">{data.candidate_questions}</div><div class="lbl">Candidates</div></div>
</div>
{_render_changes(data)}
</section>
</div>

<script>
const questions = {questions_json};
{_JS}
</script>
</body>
</html>"""


def _render_universes(data: CockpitData) -> str:
    if not data.universes:
        return "<p>Universe health not indexed.</p>"
    rows = ""
    for u in data.universes:
        rows += f"<tr><td>{u.get('universe','')}</td><td>{u.get('records',0)}</td><td>{u.get('schema_version','')}</td><td class='st-{u.get('status','').lower()}'>{u.get('status','')}</td></tr>"
    return f"<h3>Universe Health</h3><table><thead><tr><th>Universe</th><th>Records</th><th>Schema</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>"


def _render_angle(title: str, questions: list) -> str:
    """Render a question group (used internally by universes section)."""
    if not questions:
        return f"<h3>{title} (0 questions)</h3>"
    rows = "".join(
        f"<tr><td>{q.question_id}</td><td>{q.title}</td><td class='st-{q.status.lower()}'>{q.status}</td><td>{q.outcome}</td><td>{q.confidence}</td></tr>"
        for q in questions
    )
    return f"<h3>{title} ({len(questions)} questions)</h3><table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Outcome</th><th>Confidence</th></tr></thead><tbody>{rows}</tbody></table>"


def _render_universes_section(data: CockpitData) -> str:
    """Render the full 3-state 8-universe architecture view."""
    parts = []

    # 3-state structure definitions
    _UNIVERSE_MAP = {
        "PRIMARY RESEARCH": [
            ("EXECUTION", "Trade execution outcomes and lifecycle", data.execution_questions),
            ("DECISION", "Decision pipeline reasoning and arbitration", data.decision_questions),
            ("MARKET", "Market regime, phase, volatility, session", data.market_questions),
            ("STRATEGY", "Strategy selection, family, pattern behaviour", data.strategy_questions),
        ],
        "RISK / OUTCOME": [
            ("RISK", "Risk gate decisions and position sizing", [q for q in data.all_questions if "RISK" in q.angles]),
            ("OUTCOME", "Realised trade outcomes (derived from Execution)", [q for q in data.all_questions if "OUTCOME" in q.angles]),
        ],
        "SHADOW RESEARCH": [
            ("SHADOW_OUTCOME", "Counterfactual shadow simulation outcomes", [q for q in data.all_questions if "SHADOW_OUTCOME" in q.angles]),
            ("SHADOW_REALITY", "Shadow prediction vs realised outcome comparison", [q for q in data.all_questions if "SHADOW_REALITY" in q.angles]),
        ],
    }

    for state_name, universes in _UNIVERSE_MAP.items():
        parts.append(f"<h3>{state_name}</h3>")
        for uni_name, description, questions in universes:
            q_count = len(questions)
            if q_count == 0:
                status_class = "st-blocked"
                status_label = "UNRESEARCHED"
            else:
                ready = sum(1 for q in questions if q.status == "COMPLETE")
                if ready == q_count:
                    status_class = "st-complete"
                    status_label = f"{ready}/{q_count} COMPLETE"
                elif ready > 0:
                    status_class = "st-inconclusive"
                    status_label = f"{ready}/{q_count} COMPLETE"
                else:
                    status_class = "st-not_run"
                    status_label = f"0/{q_count} COMPLETE"

            parts.append(f"""<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;margin:8px 0">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><strong>{uni_name}</strong> <span style="color:#8b949e;font-size:12px">— {description}</span></div>
<div><span class="{status_class}" style="font-weight:600">{status_label}</span></div>
</div>
<div style="margin-top:6px;font-size:12px;color:#8b949e">Questions: {q_count} | IDs: {', '.join(q.question_id for q in questions[:8])}{'...' if q_count > 8 else ''}</div>
</div>""")

            if q_count == 0:
                parts.append(f'<p style="color:#f85149;font-size:13px;margin:4px 0 12px 16px">No research questions target this universe. Research coverage gap.</p>')

    # Cross-universe questions
    cross = data.cross_angle_questions
    if cross:
        parts.append(f"<h3>CROSS-UNIVERSE ({len(cross)} questions)</h3>")
        rows = "".join(
            f"<tr><td>{q.question_id}</td><td>{q.title}</td><td>{', '.join(q.angles)}</td><td class='st-{q.status.lower()}'>{q.status}</td></tr>"
            for q in cross
        )
        parts.append(f"<table><thead><tr><th>ID</th><th>Title</th><th>Universes</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>")

    return "\n".join(parts)


def _render_question_rows(questions: list) -> str:
    return "".join(
        f"<tr class='q-row' data-status='{q.status}' data-search='{q.question_id} {q.title} {q.conclusion}'>"
        f"<td>{q.question_id}</td><td>{q.title}</td>"
        f"<td>{', '.join(q.angles)}</td>"
        f"<td class='st-{q.status.lower()}'>{q.status}</td>"
        f"<td>{q.outcome}</td><td>{q.confidence}</td><td>{q.sample_size}</td></tr>"
        for q in questions
    )


def _render_run_rows(runs: list) -> str:
    return "".join(
        f"<tr><td>{r['run_id']}</td><td>{r['timestamp']}</td>"
        f"<td>{r['questions_requested']}</td><td>{r['questions_executed']}</td>"
        f"<td>{r['questions_blocked']}</td><td>{r['questions_inconclusive']}</td>"
        f"<td>{r['duration_seconds']:.1f}s</td></tr>"
        for r in runs
    )


def _render_health(data: CockpitData) -> str:
    return f"""<div class="cards">
<div class="card complete"><div class="num">{data.populations_valid}</div><div class="lbl">Valid Populations</div></div>
<div class="card blocked"><div class="num">{data.populations_empty}</div><div class="lbl">Empty</div></div>
<div class="card inconclusive"><div class="num">{data.populations_degraded}</div><div class="lbl">Degraded</div></div>
</div>"""


def _render_correlation(data: CockpitData) -> str:
    c = data.correlation_summary
    if not c:
        return "<p>Correlation audit not available.</p>"
    return f"""<table><tbody>
<tr><td>Classification</td><td>{c.get('classification','')}</td></tr>
<tr><td>Coverage Rate</td><td>{c.get('coverage_rate',0):.1%}</td></tr>
<tr><td>Correlated</td><td>{c.get('correlated',0)}</td></tr>
<tr><td>Uncorrelated</td><td>{c.get('uncorrelated',0)}</td></tr>
<tr><td>Method</td><td>{c.get('method','')}</td></tr>
</tbody></table>"""


def _render_changes(data: CockpitData) -> str:
    if not data.finding_changes:
        return "<p>No finding changes between runs.</p>"
    rows = "".join(
        f"<tr><td>{c['question_id']}</td><td>{c['title']}</td><td>{c['change']}</td></tr>"
        for c in data.finding_changes
    )
    return f"<h3>Finding Changes</h3><table><thead><tr><th>Question</th><th>Title</th><th>Change</th></tr></thead><tbody>{rows}</tbody></table>"


def _render_discovery(data: CockpitData) -> str:
    """Render the Discovery section: triggers, investigations, knowledge."""
    parts = []

    # Cycle engine status
    parts.append(f"""<h3>Research Cycle Engine</h3>
<table><tbody>
<tr><td>Total cycles</td><td>{data.cycle_total}</td></tr>
<tr><td>Last cycle</td><td>{data.cycle_last_id}</td></tr>
<tr><td>Last timestamp</td><td>{data.cycle_last_timestamp[:19] if data.cycle_last_timestamp else 'never'}</td></tr>
<tr><td>Status</td><td>{data.cycle_last_status}</td></tr>
<tr><td>Triggers detected (last)</td><td>{data.cycle_last_triggers}</td></tr>
<tr><td>Investigated (last)</td><td>{data.cycle_last_investigated}</td></tr>
</tbody></table>""")

    # Triggers
    parts.append(f"""<h3>Finding Triggers</h3>
<div class="cards">
<div class="card"><div class="num">{data.triggers_total}</div><div class="lbl">Total</div></div>
<div class="card complete"><div class="num">{data.triggers_eligible}</div><div class="lbl">Eligible</div></div>
<div class="card inconclusive"><div class="num">{data.triggers_dismissed}</div><div class="lbl">Dismissed</div></div>
<div class="card"><div class="num">{data.triggers_investigated}</div><div class="lbl">Investigated</div></div>
</div>""")

    if data.triggers:
        rows = "".join(
            f"<tr><td>{t['trigger_id'][:20]}</td><td>{t['title']}</td><td>{t['category']}</td><td>{t['status']}</td><td>{t['sample_size']}</td></tr>"
            for t in data.triggers[:20]
        )
        parts.append(f"<table><thead><tr><th>ID</th><th>Title</th><th>Category</th><th>Status</th><th>N</th></tr></thead><tbody>{rows}</tbody></table>")

    # Knowledge / Findings
    if data.knowledge_findings:
        parts.append("<h3>Research Findings (Knowledge Map)</h3>")
        rows = "".join(
            f"<tr><td>{f['hypothesis_id'][:15]}</td><td>{f['title']}</td><td class='st-{f['conclusion'].lower()}'>{f['conclusion']}</td><td>{f['confidence']}</td><td>{f['mean_r']:+.4f}</td><td>{f['n']}</td></tr>"
            for f in data.knowledge_findings
        )
        parts.append(f"<table><thead><tr><th>Hypothesis</th><th>Title</th><th>Conclusion</th><th>Confidence</th><th>Mean R</th><th>N</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        parts.append("<p>No lifecycle findings yet.</p>")

    return "\n".join(parts)


def _render_candidates(data: CockpitData) -> str:
    """Render the Candidates section."""
    parts = []

    parts.append(f"""<div class="cards">
<div class="card"><div class="num">{data.candidates_total}</div><div class="lbl">Total Candidates</div></div>
</div>""")

    if data.candidates:
        rows = "".join(
            f"<tr><td>{c['candidate_id']}</td><td>{c['status']}</td><td>{c['type']}</td><td>{c['risk_level']}</td><td>{c['created_at']}</td><td>{c['description']}</td><td>{c['validations']}</td></tr>"
            for c in data.candidates
        )
        parts.append(f"""<table><thead><tr><th>ID</th><th>Status</th><th>Type</th><th>Risk</th><th>Created</th><th>Description</th><th>Validations</th></tr></thead><tbody>{rows}</tbody></table>""")

        # Explain shadow-testability
        parts.append("""<h3>Shadow-Testable Types</h3>
<p>Candidates with these types can be prospectively shadow-tested: <strong>direction_inversion, geometry_modification, regime_conditioning, symbol_exclusion</strong></p>
<p>Candidates with these types require manual review (not shadow-testable): <em>pattern_weighting, score_recalibration, research_recommendation</em></p>""")
    else:
        parts.append("<p>No optimisation candidates registered.</p>")

    return "\n".join(parts)


def _render_shadow_reality(data: CockpitData) -> str:
    """Render the Shadow↔Reality section."""
    parts = []

    parts.append(f"""<div class="cards">
<div class="card complete"><div class="num">{data.sr_matched}</div><div class="lbl">Matched Pairs</div></div>
<div class="card inconclusive"><div class="num">{data.sr_shadow_only}</div><div class="lbl">Shadow Only</div></div>
<div class="card blocked"><div class="num">{data.sr_real_only}</div><div class="lbl">Real Only</div></div>
</div>""")

    stats = data.sr_stats
    if stats and stats.get("n", 0) > 0:
        parts.append(f"""<h3>Comparison Statistics (N={stats['n']})</h3>
<table><tbody>
<tr><td>Mean delta R (shadow - real)</td><td><strong>{stats.get('mean_delta_r', 0):+.4f}</strong></td></tr>
<tr><td>Median delta R</td><td>{stats.get('median_delta_r', 0):+.4f}</td></tr>
<tr><td>Std delta R</td><td>{stats.get('std_delta_r', 0):.4f}</td></tr>
<tr><td>Min / Max</td><td>{stats.get('min_delta_r', 0):+.4f} / {stats.get('max_delta_r', 0):+.4f}</td></tr>
<tr><td>Shadow predicted better</td><td>{stats.get('shadow_better_count', 0)}</td></tr>
<tr><td>Real outperformed shadow</td><td>{stats.get('real_better_count', 0)}</td></tr>
<tr><td>Exit reason agreement</td><td>{stats.get('exit_reason_match_rate', 0):.0%}</td></tr>
<tr><td>Geometry agreement</td><td>{stats.get('geometry_match_rate', 0):.0%}</td></tr>
<tr><td>Mean entry slippage</td><td>{stats.get('mean_entry_slippage', 0):.6f}</td></tr>
</tbody></table>
<p class="meta">delta_r = shadow_r - realised_gross_r. Negative means shadow underestimates real performance. This is an observed divergence, not a causal attribution.</p>""")
    else:
        parts.append("<p>Insufficient matched pairs for comparison statistics.</p>")

    return "\n".join(parts)


def _render_prop_readiness(data: CockpitData) -> str:
    """Render the Prop Readiness Gate section."""
    status = data.prop_readiness_status or "UNKNOWN"
    if status == "READY":
        status_class = "st-complete"
    elif status == "NOT_READY":
        status_class = "st-blocked"
    else:
        status_class = "st-inconclusive"

    parts = []
    parts.append(f'<div class="cards"><div class="card {status_class.replace("st-","")}">'
                 f'<div class="num" style="font-size:20px">{status}</div><div class="lbl">Readiness Status</div></div></div>')

    # Evidence table
    parts.append("""<h3>Evidence Requirements</h3><table>
<thead><tr><th>Requirement</th><th>Threshold</th><th>Current</th><th>Status</th></tr></thead><tbody>""")

    # Realised expectancy
    exp = data.prop_realised_expectancy
    exp_str = f"{exp:+.4f}R (N={data.prop_realised_n})" if exp is not None else "NOT COMPUTED"
    exp_status = "st-complete" if (exp is not None and exp > 0) else "st-blocked"
    parts.append(f"<tr><td>Positive realised expectancy</td><td>R &gt; 0</td><td>{exp_str}</td><td class='{exp_status}'>{'PASS' if exp and exp > 0 else 'FAIL'}</td></tr>")

    # Shadow calibration
    sr_str = f"{data.prop_shadow_calibration_n}/50 pairs"
    sr_status = "st-complete" if data.prop_shadow_calibration_ok else "st-inconclusive"
    parts.append(f"<tr><td>Shadow model calibrated</td><td>N &ge; 50 matched pairs</td><td>{sr_str}</td><td class='{sr_status}'>{'PASS' if data.prop_shadow_calibration_ok else 'PENDING'}</td></tr>")

    # Research running
    cycle_ok = data.cycle_total >= 1
    parts.append(f"<tr><td>Research engine active</td><td>&ge; 1 cycle</td><td>{data.cycle_total} cycles</td><td class='{'st-complete' if cycle_ok else 'st-blocked'}'>{'PASS' if cycle_ok else 'FAIL'}</td></tr>")

    parts.append("</tbody></table>")

    # Reasons blocking
    if data.prop_readiness_reasons:
        parts.append("<h3>Blocking Reasons</h3><ul>")
        for r in data.prop_readiness_reasons:
            parts.append(f"<li>{r}</li>")
        parts.append("</ul>")

    parts.append('<p class="meta">This is an evidence assessment, not financial advice. Prop-firm deployment remains a human decision.</p>')
    return "\n".join(parts)


def _q_to_dict(q) -> dict:
    return {
        "id": q.question_id, "title": q.title, "status": q.status,
        "outcome": q.outcome, "confidence": q.confidence,
        "universes": q.angles, "conclusion": q.conclusion,
    }


_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px;line-height:1.5}
header{margin-bottom:20px}
h1{color:#58a6ff;font-size:24px}
h2{color:#58a6ff;margin:20px 0 12px;font-size:20px}
h3{color:#8b949e;margin:16px 0 8px;font-size:16px}
.meta{color:#8b949e;font-size:13px;margin-top:4px}
nav{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.nav-btn{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px}
.nav-btn.active,.nav-btn:hover{background:#388bfd;color:#fff;border-color:#388bfd}
.search-bar{margin-bottom:16px}
.search-bar input{width:100%;max-width:400px;padding:8px 12px;background:#161b22;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:14px}
.section{display:none}
.section.active{display:block}
.cards{display:flex;gap:12px;margin:12px 0;flex-wrap:wrap}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 20px;min-width:120px;text-align:center}
.card .num{font-size:28px;font-weight:700;color:#c9d1d9}
.card .lbl{font-size:12px;color:#8b949e;margin-top:4px}
.card.complete .num{color:#3fb950}
.card.inconclusive .num{color:#d29922}
.card.blocked .num{color:#f85149}
.card.error .num{color:#f85149}
.card.notrun .num{color:#8b949e}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}
th{background:#161b22;padding:8px;text-align:left;border-bottom:1px solid #30363d;color:#8b949e}
td{padding:8px;border-bottom:1px solid #21262d}
tr:hover{background:#161b22}
.st-complete{color:#3fb950}
.st-inconclusive{color:#d29922}
.st-blocked{color:#f85149}
.st-error{color:#f85149}
.st-not_run{color:#8b949e}
.st-valid{color:#3fb950}
.st-degraded{color:#d29922}
.st-empty{color:#8b949e}
.filter-row{margin-bottom:12px;display:flex;gap:6px}
.filter-row button{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px}
.filter-row button:hover{background:#30363d}
"""

_JS = """
function showSection(id){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  event.target.classList.add('active');
}
function filterQuestions(){
  const q=document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.q-row').forEach(r=>{
    r.style.display=r.dataset.search.toLowerCase().includes(q)?'':'none';
  });
}
function filterByStatus(s){
  document.querySelectorAll('.q-row').forEach(r=>{
    r.style.display=(s==='all'||r.dataset.status===s)?'':'none';
  });
}
"""
