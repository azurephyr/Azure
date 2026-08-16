"""
RC1 Certification orchestrator — runs every harness and prints a
combined PASS/FAIL/KNOWN_LIMITATION report.

Usage:
  PYTHONIOENCODING=utf-8 python tests/certification/run_all.py

Exit 0 when nothing fails (KNOWN_LIMITATION counts as not-failed).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

_orig_pythonioencoding = os.environ.get("PYTHONIOENCODING")
os.environ["PYTHONIOENCODING"] = "utf-8"

ROOT = Path(__file__).resolve().parent.parent.parent
_orig_sys_path = list(sys.path)
sys.path.insert(0, str(ROOT))

DIR = Path(__file__).resolve().parent
SUITES = [
    DIR / "test_rc1_certification.py",
    DIR / "test_rc1_subsystems.py",
    DIR / "test_rc1_stress.py",
    DIR / "test_rc1_module_coverage.py",
    # Apex RC1 permanent regression (memory race, validator, health auth)
    DIR / "test_rc1_apex_regression.py",
]


def run(label: str, path: Path):
    print()
    print("=" * 70)
    print(f"RUN: {label}")
    print("=" * 70)
    t0 = time.perf_counter()
    rc = subprocess.run([sys.executable, str(path)],
                       cwd=str(ROOT), env=os.environ.copy())
    dt = time.perf_counter() - t0
    return rc.returncode, dt


def main():
    try:
        print("RC1 Certification Suite Orchestrator")
        print(f"Project root: {ROOT}")
        starts = time.perf_counter()
        results = []
        for suite in SUITES:
            label = suite.name
            rc, dt = run(label, suite)
            results.append((label, rc, dt))
        total_dt = time.perf_counter() - starts
        print()
        print("=" * 70)
        print(f"OVERALL: {len(SUITES)} suites in {total_dt:.1f}s")
        print("=" * 70)
        for label, rc, dt in results:
            flag = "PASS" if rc == 0 else "FAIL" if rc == 1 else f"EXIT({rc})"
            print(f"  {label:38s} {flag:8s} {dt:5.1f}s")
        fails = sum(1 for _, rc, _ in results if rc not in (0,))
        print()
        if fails == 0:
            print(f"ALL {len(SUITES)} SUITES PASSED (exit 0 each; "
                  f"KNOWN_LIMITATION accepted)")
        else:
            print(f"{fails}/{len(SUITES)} suites exited non-zero")
        sys.exit(0 if fails == 0 else 1)
    finally:
        if _orig_pythonioencoding:
            os.environ["PYTHONIOENCODING"] = _orig_pythonioencoding
        else:
            os.environ.pop("PYTHONIOENCODING", None)
        sys.path[:] = _orig_sys_path


if __name__ == "__main__":
    main()
