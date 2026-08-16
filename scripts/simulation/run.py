"""CLI entry point for the simulation server."""

import argparse
import asyncio
import sys

from .core import SimEnv
from .report import print_summary, to_html, to_json
from .runner import configure_ctx, run_batch


async def main():
    parser = argparse.ArgumentParser(description="Azure AI Simulation Server")
    parser.add_argument("--subsystem", "-s", help="Filter by subsystem")
    parser.add_argument("--report", "-r", action="store_true", help="Generate HTML report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Detailed per-scenario output")
    parser.add_argument("--json", "-j", action="store_true", help="Write JSON results")
    parser.add_argument("--scenarios", "-t", help="Comma-separated scenario IDs")
    parser.add_argument("--mode", "-m", default="direct", choices=["direct", "pipeline", "auto"],
                        help="Execution mode")
    parser.add_argument("--list", "-l", action="store_true", help="List available scenarios")
    parser.add_argument("--real-agent", "-a", action="store_true",
                        help="Use real AzureAgent instead of FakeAgent")
    parser.add_argument("--improve", "-i", action="store_true",
                        help="Generate improvement analysis after run")
    args = parser.parse_args()

    from .catalog import CATALOG

    if args.list:
        print(f"Available scenarios ({len(CATALOG)}):")
        for s in CATALOG:
            tags = ", ".join(s.tags) if s.tags else ""
            print(f"  {s.id:14s} {s.name:40s} [{s.subsystem:12s}] {tags}")
        return

    print("Azure AI Discord Bot — Simulation Server")
    print("=" * 50)

    print("  Setting up simulation environment...")
    env = SimEnv()
    env.setup()
    configure_ctx(env)
    print(f"  Guild: {env.guild.name}  Members: {len(env.members)}  Channels: {len(env.channels)}")

    print()

    scenarios = list(CATALOG)

    # Optionally replace FakeAgent with real AzureAgent
    if args.real_agent:
        print("  Initializing real AzureAgent (this may take a moment)...")
        from .real_agent import setup_real_agent
        ok = setup_real_agent(env)
        if ok:
            print("  Real AzureAgent is ready!")
            # Relax keyword assertions for real LLM output
            for s in scenarios:
                s.lenient = True
        else:
            print("  Real AzureAgent unavailable — using FakeAgent")

    if args.subsystem:
        scenarios = [s for s in scenarios if s.subsystem == args.subsystem]
        if not scenarios:
            print(f"  No scenarios for subsystem '{args.subsystem}'")
            subsystems = sorted({s.subsystem for s in CATALOG})
            print(f"  Available: {subsystems}")
            sys.exit(1)

    if args.scenarios:
        ids = set(x.strip() for x in args.scenarios.split(","))
        scenarios = [s for s in scenarios if s.id in ids]
        if not scenarios:
            print(f"  No scenarios matching IDs: {args.scenarios}")
            sys.exit(1)

    if args.mode != "auto":
        for s in scenarios:
            s.mode = args.mode

    mode_label = "real-agent" if args.real_agent else args.mode
    print(f"  Running {len(scenarios)} scenarios (mode: {mode_label})...\n")

    results = await run_batch(env, scenarios)

    print_summary(results)

    if args.report:
        path = to_html(results)
        print(f"  HTML report: {path}")
    if args.json:
        path = to_json(results)
        print(f"  JSON results: {path}")

    if args.improve:
        from .improvement import analyze_results, print_analysis, to_improvement_report
        analysis = analyze_results(results)
        print_analysis(analysis)
        imp_path = to_improvement_report(results)
        print(f"  Improvement report: {imp_path}")

    failed = sum(1 for r in results if not r.passed)
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
