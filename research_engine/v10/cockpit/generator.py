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
<button class="nav-btn" onclick="showSection('angles')">Four Angles</button>
<button class="nav-btn" onclick="showSection('questions')">Questions</button>
<button class="nav-btn" onclick="showSection('runs')">Run History</button>
<button class="nav-btn" onclick="showSection('health')">Health</button>
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
<div class="card"><div class="num">{data.candidate_questions}</div><div class="lbl">Candidates</div></div>
<div class="card"><div class="num">{len(data.run_history)}</div><div class="lbl">Runs</div></div>
</div>
{_render_universes(data)}
</section>

<!-- FOUR ANGLES -->
<section id="angles" class="section">
<h2>Four-Angle Research View</h2>
{_render_angle("Execution", data.execution_questions)}
{_render_angle("Decision", data.decision_questions)}
{_render_angle("Market", data.market_questions)}
{_render_angle("Strategy", data.strategy_questions)}
{_render_angle("Cross-Angle", data.cross_angle_questions)}
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
<thead><tr><th>ID</th><th>Title</th><th>Angles</th><th>Status</th><th>Outcome</th><th>Confidence</th><th>Sample</th></tr></thead>
<tbody>
{_render_question_rows(data.all_questions)}
</tbody>
</table>
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
    if not questions:
        return f"<h3>{title} (0 questions)</h3>"
    rows = "".join(
        f"<tr><td>{q.question_id}</td><td>{q.title}</td><td class='st-{q.status.lower()}'>{q.status}</td><td>{q.outcome}</td><td>{q.confidence}</td></tr>"
        for q in questions
    )
    return f"<h3>{title} ({len(questions)} questions)</h3><table><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Outcome</th><th>Confidence</th></tr></thead><tbody>{rows}</tbody></table>"


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


def _q_to_dict(q) -> dict:
    return {
        "id": q.question_id, "title": q.title, "status": q.status,
        "outcome": q.outcome, "confidence": q.confidence,
        "angles": q.angles, "conclusion": q.conclusion,
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
