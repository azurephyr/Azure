"""Simulation results analysis — identifies patterns and suggests improvements."""

from collections import Counter
from datetime import datetime

from .runner import SimResult


def analyze_results(results: list[SimResult]) -> dict:
    """Analyze simulation results and generate improvement suggestions."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = [r for r in results if not r.passed]

    by_subsystem = Counter(r.subsystem for r in results)
    by_subsystem_pass = Counter(r.subsystem for r in results if r.passed)

    suggestions = []
    subsystem_stats = {}

    for sub in sorted(by_subsystem):
        t = by_subsystem[sub]
        p = by_subsystem_pass[sub]
        f = t - p
        rate = p / t * 100 if t else 0.0

        subsystem_stats[sub] = {"total": t, "passed": p, "failed": f, "rate": f"{rate:.1f}%"}

        if rate < 80:
            suggestions.append({
                "priority": "high",
                "area": sub,
                "finding": f"{sub}: {f}/{t} scenarios failed ({rate:.1f}% pass rate)",
                "suggestion": f"Review {sub} subsystem logic and agent response configuration",
            })
        elif rate < 100:
            suggestions.append({
                "priority": "medium",
                "area": sub,
                "finding": f"{sub}: {f}/{t} scenarios failed ({rate:.1f}% pass rate)",
                "suggestion": f"Review individual failing scenarios in {sub}",
            })

    for r in failed:
        if r.errors:
            for e in r.errors[:2]:
                suggestions.append({
                    "priority": "high" if "exception" in e.lower() else "medium",
                    "area": r.subsystem,
                    "finding": f"{r.scenario_id} ({r.scenario_name}): {e[:200]}",
                    "suggestion": f"Debug {r.scenario_id} — check scenario config and bot response",
                })
        if r.assertion_results:
            for key, ar in r.assertion_results.items():
                if not ar["passed"]:
                    suggestions.append({
                        "priority": "low",
                        "area": r.subsystem,
                        "finding": f"{r.scenario_id}: assertion '{key}' failed (expected {ar['expected']})",
                        "suggestion": f"Update assertion for {r.scenario_id} or adjust response mapping",
                    })

    latency_values = [r.latency for r in results if r.latency > 0]
    avg_latency = sum(latency_values) / len(latency_values) if latency_values else 0.0
    max_latency = max(latency_values) if latency_values else 0.0

    return {
        "summary": {
            "total": total,
            "passed": passed,
            "failed": len(failed),
            "pass_rate": f"{passed / total * 100:.1f}%" if total else "0%",
            "avg_latency": f"{avg_latency * 1000:.0f}ms" if avg_latency < 1 else f"{avg_latency:.1f}s",
            "max_latency": f"{max_latency * 1000:.0f}ms" if max_latency < 1 else f"{max_latency:.1f}s",
            "timestamp": datetime.now().isoformat(),
        },
        "subsystems": subsystem_stats,
        "suggestions": sorted(suggestions, key=lambda s: (0 if s["priority"] == "high" else 1 if s["priority"] == "medium" else 2)),
    }


def print_analysis(analysis: dict):
    """Print analysis to console."""
    s = analysis["summary"]
    print()
    print("=" * 56)
    print("  IMPROVEMENT ANALYSIS")
    print("=" * 56)
    print(f"  Pass rate: {s['pass_rate']}  ({s['passed']}/{s['total']})")
    print(f"  Avg latency: {s['avg_latency']}  Max: {s['max_latency']}")
    print()

    if analysis["subsystems"]:
        print("  -- Subsystem breakdown --")
        for sub, stats in sorted(analysis["subsystems"].items()):
            bar = "#" * (int(float(stats["rate"].rstrip("%")) / 10))
            print(f"    {sub:15s}  {stats['rate']:>6s}  {bar}")
    print()

    if analysis["suggestions"]:
        print("  -- Improvement suggestions --")
        for sug in analysis["suggestions"]:
            icon = "!" if sug["priority"] == "high" else "~" if sug["priority"] == "medium" else "+"
            print(f"  {icon} [{sug['priority'].upper()}] {sug['area']}:")
            print(f"       {sug['finding'][:100]}")
            print(f"       → {sug['suggestion'][:100]}")
        print()


def to_improvement_report(results: list[SimResult], path: str = "improvement_report.html"):
    """Generate an HTML improvement report."""
    analysis = analyze_results(results)
    s = analysis["summary"]

    subsys_rows = ""
    for sub, stats in sorted(analysis["subsystems"].items()):
        bar_width = int(float(stats["rate"].rstrip("%")))
        subsys_rows += f"""
  <tr><td>{sub}</td><td>{stats['passed']}/{stats['total']}</td>
      <td>{stats['rate']}</td>
      <td><div class="bar"><div class="fill" style="width:{bar_width}%"></div></div></td></tr>"""

    sug_rows = ""
    for sug in analysis["suggestions"]:
        cls = sug["priority"]
        sug_rows += f"""
  <tr class="{cls}"><td><span class="badge {cls}">{sug["priority"]}</span></td>
      <td>{sug["area"]}</td>
      <td>{sug["finding"][:120]}</td>
      <td>{sug["suggestion"][:120]}</td></tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">
<title>Improvement Report — Azure AI Simulation</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#0d1117; color:#c9d1d9; padding:2rem; }}
  h1 {{ color:#58a6ff; }} h2 {{ color:#8b949e; font-weight:400; margin:1rem 0; }}
  .summary {{ display:flex; gap:2rem; margin:1.5rem 0; flex-wrap:wrap; }}
  .stat {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:1rem 1.5rem; min-width:120px; text-align:center; }}
  .stat .value {{ font-size:1.8rem; font-weight:700; }}
  .stat .label {{ font-size:0.8rem; color:#8b949e; }}
  table {{ width:100%; border-collapse:collapse; margin:1rem 0; }}
  th {{ text-align:left; padding:0.5rem; border-bottom:2px solid #30363d; color:#8b949e; font-size:0.75rem; text-transform:uppercase; }}
  td {{ padding:0.5rem; border-bottom:1px solid #21262d; font-size:0.85rem; }}
  .bar {{ background:#21262d; border-radius:4px; height:16px; overflow:hidden; }}
  .fill {{ background:#3fb950; height:100%; border-radius:4px; }}
  .badge {{ display:inline-block; padding:0.1rem 0.4rem; border-radius:3px; font-size:0.7rem; font-weight:700; }}
  .badge.high {{ background:rgba(248,81,73,0.2); color:#f85149; }}
  .badge.medium {{ background:rgba(210,153,34,0.2); color:#d29922; }}
  .badge.low {{ background:rgba(63,185,80,0.2); color:#3fb950; }}
  tr.high td {{ background:rgba(248,81,73,0.03); }}
</style></head>
<body>
<h1>Azure AI Simulation — Improvement Report</h1>
<h2>{s['pass_rate']} pass rate · {s['passed']}/{s['total']} scenarios</h2>
<div class="summary">
  <div class="stat"><div class="value">{s['passed']}</div><div class="label">Passed</div></div>
  <div class="stat"><div class="value">{s['failed']}</div><div class="label">Failed</div></div>
  <div class="stat"><div class="value">{s['avg_latency']}</div><div class="label">Avg Latency</div></div>
</div>
<h2>Subsystems</h2>
<table><thead><tr><th>Subsystem</th><th>Pass</th><th>Rate</th><th>Trend</th></tr></thead>
<tbody>{subsys_rows}</tbody></table>
<h2>Improvement Suggestions ({len(analysis['suggestions'])})</h2>
<table><thead><tr><th>Priority</th><th>Area</th><th>Finding</th><th>Suggestion</th></tr></thead>
<tbody>{sug_rows}</tbody></table>
<p style="margin-top:2rem;color:#484f58;font-size:0.8rem;">{s['timestamp']}</p>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
