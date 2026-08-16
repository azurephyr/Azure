"""Report generation — console table, HTML report, JSON output."""

import json
from collections import Counter

from .runner import SimResult


def _fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


def print_summary(results: list[SimResult]):
    """Print a human-readable table to stdout."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    rate = passed / total * 100 if total else 0.0
    avg_latency = sum(r.latency for r in results) / total if total else 0.0

    hr = "=" * 56
    print()
    print(hr)
    print("  SIMULATION RESULTS")
    print(hr)
    print(f"  Passed: {passed}/{total}  ({rate:.1f}%)")
    print(f"  Avg latency: {_fmt_duration(avg_latency)}")
    print()

    if total == 0:
        return

    by_subsystem = Counter(r.subsystem for r in results)
    by_subsystem_pass = Counter(r.subsystem for r in results if r.passed)
    print("  -- By subsystem --")
    for sub in sorted(by_subsystem):
        p = by_subsystem_pass[sub]
        t = by_subsystem[sub]
        print(f"    {sub:15s}  {p}/{t}  ({p / t * 100:.0f}%)")
    print()

    print("  -- Details --")
    for r in results:
        icon = "PASS" if r.passed else "FAIL"
        print(f"  [{icon}] {r.scenario_id:12s} {r.scenario_name:35s} "
              f"({_fmt_duration(r.latency)})")
        if not r.passed and r.errors:
            for e in r.errors[:3]:
                print(f"           error: {e[:120]}")
        if not r.passed and r.assertion_results:
            for key, ar in r.assertion_results.items():
                if not ar["passed"]:
                    print(f"           assert {key}: expected={ar['expected']!r}")
    print()


def to_json(results: list[SimResult], path: str = "simulation_results.json"):
    """Write results as JSON."""
    data = {
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "pass_rate": sum(1 for r in results if r.passed) / len(results) * 100 if results else 0.0,
            "avg_latency": sum(r.latency for r in results) / len(results) if results else 0.0,
        },
        "scenarios": [
            {
                "id": r.scenario_id,
                "name": r.scenario_name,
                "subsystem": r.subsystem,
                "passed": r.passed,
                "latency": r.latency,
                "response": r.response,
                "errors": r.errors,
                "assertions": r.assertion_results,
            }
            for r in results
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def to_html(results: list[SimResult], path: str = "simulation_report.html"):
    """Generate a self-contained HTML report (no external deps)."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    rate = passed / total * 100 if total else 0.0
    avg_lat = sum(r.latency for r in results) / total if total else 0.0

    def _lat(s):
        return _fmt_duration(s)

    rows = ""
    for r in results:
        status_cls = "pass" if r.passed else "fail"
        badge = "PASS" if r.passed else "FAIL"
        errors_html = ""
        if r.errors:
            errors_html = "<div class='errors'><ul>" + "".join(
                f"<li>{e[:200]}</li>" for e in r.errors[:3]
            ) + "</ul></div>"
        asserts_html = ""
        if r.assertion_results:
            cells = ""
            for key, ar in r.assertion_results.items():
                cls = "pass" if ar["passed"] else "fail"
                cells += f"<span class='assert {cls}'>{key}: {ar['passed']}</span> "
            asserts_html = f"<div class='asserts'>{cells}</div>"
        rows += f"""\
<tr class="{status_cls}">
  <td><span class="badge {status_cls}">{badge}</span></td>
  <td>{r.scenario_id}</td>
  <td>{r.scenario_name}</td>
  <td>{r.subsystem}</td>
  <td>{_lat(r.latency)}</td>
  <td><pre class="response">{r.response or ''}</pre>{errors_html}{asserts_html}</td>
</tr>"""

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Azure AI Simulation Report</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; padding: 2rem; }}
  h1 {{ color: #58a6ff; margin-bottom: 0.5rem; }}
  h2 {{ color: #8b949e; font-weight: 400; margin: 1rem 0; }}
  .summary {{ display: flex; gap: 2rem; margin: 1.5rem 0; flex-wrap: wrap; }}
  .stat {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem 1.5rem; min-width: 120px; text-align: center; }}
  .stat .value {{ font-size: 2rem; font-weight: 700; }}
  .stat .label {{ font-size: 0.8rem; color: #8b949e; margin-top: 0.25rem; }}
  .stat.pass .value {{ color: #3fb950; }}
  .stat.fail .value {{ color: #f85149; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
  th {{ text-align: left; padding: 0.75rem 0.5rem; border-bottom: 2px solid #30363d; color: #8b949e; font-size: 0.8rem; text-transform: uppercase; }}
  td {{ padding: 0.5rem; border-bottom: 1px solid #21262d; }}
  tr.fail {{ background: rgba(248,81,73,0.05); }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.7rem; font-weight: 700; }}
  .badge.pass {{ background: rgba(63,185,80,0.2); color: #3fb950; }}
  .badge.fail {{ background: rgba(248,81,73,0.2); color: #f85149; }}
  .response {{ max-height: 100px; overflow-y: auto; background: #161b22; padding: 0.5rem; border-radius: 4px; font-size: 0.8rem; color: #c9d1d9; }}
  .errors {{ color: #f85149; font-size: 0.8rem; margin-top: 0.25rem; }}
  .asserts {{ font-size: 0.8rem; margin-top: 0.25rem; }}
  .assert {{ margin-right: 0.5rem; }}
  .assert.pass {{ color: #3fb950; }}
  .assert.fail {{ color: #f85149; }}
</style>
</head>
<body>
<h1>Azure AI Discord Bot — Simulation Report</h1>
<h2>{passed}/{total} scenarios passed ({rate:.1f}%) — avg {_lat(avg_lat)}</h2>
<div class="summary">
  <div class="stat pass"><div class="value">{passed}</div><div class="label">Passed</div></div>
  <div class="stat fail"><div class="value">{total - passed}</div><div class="label">Failed</div></div>
  <div class="stat"><div class="value">{total}</div><div class="label">Total</div></div>
  <div class="stat"><div class="value">{_lat(avg_lat)}</div><div class="label">Avg Latency</div></div>
</div>
<table>
<thead><tr><th>Status</th><th>ID</th><th>Name</th><th>Subsystem</th><th>Time</th><th>Response / Errors</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p style="margin-top:2rem;color:#484f58;font-size:0.8rem;">Generated by Azure AI Simulation · Phase A</p>
</body>
</html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
