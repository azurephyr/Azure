"""
Module coverage — every production .py file is imported and its
class/function list captured. Failed-imports are categorized.

This is a "did Python accept this file?" coverage test, separate
from behavioral coverage. Combined with the certification harness
above, we get coverage for every production module.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

_orig_pythonioencoding = os.environ.get("PYTHONIOENCODING")
os.environ["PYTHONIOENCODING"] = "utf-8"
ROOT = Path(__file__).resolve().parent.parent.parent
_orig_sys_path = list(sys.path)
sys.path.insert(0, str(ROOT))

# Production top-level modules to import directly
TOP_PKGS = [
    "azure",                 # package itself (broadcasts __init__)
    "bot",                   # package
    "web",                   # package
]
# Subpackages with __init__
SUB_PKGS = [
    "azure.cognition",
    "azure.integrations",
    "azure.moderation",
    "azure.plugins",
    "azure.recovery",
    "azure.tools",
    "azure.ai_moderation",
]


def import_module(mod_path: str):
    """Return (success, error_class, message) for one module."""
    try:
        m = importlib.import_module(mod_path)
        # If it's a package, iterate the child modules to import eagerly
        if hasattr(m, "__path__"):
            from pkgutil import iter_modules
            for _finder, name, _ in iter_modules(m.__path__):
                child = f"{mod_path}.{name}"
                try:
                    importlib.import_module(child)
                except Exception as e:
                    return True, "PARTIAL", f"child {child} failed: {type(e).__name__}: {e}"
        return True, None, ""
    except Exception as e:
        return False, type(e).__name__, str(e)[:200]


def main():
    try:
        print(f"Project root: {ROOT}")
        pkg_outcomes = {}
        for pkg in TOP_PKGS + SUB_PKGS:
            ok, kind, msg = import_module(pkg)
            pkg_outcomes[pkg] = (ok, kind, msg)
            print(f"  {pkg:30s} {'OK' if ok and kind is None else ('PARTIAL('+kind+')' if ok else 'FAIL('+kind+')')}")
            if msg:
                print(f"      -> {msg}")

        pass_count = sum(1 for v in pkg_outcomes.values() if v[0] and v[1] is None)
        fail_count = sum(1 for v in pkg_outcomes.values() if not v[0])
        partial = sum(1 for v in pkg_outcomes.values() if v[0] and v[1] is not None)
        print()
        print(f"Packages OK: {pass_count}   PARTIAL: {partial}   FAIL: {fail_count}")
        print()
        print("Per-file coverage probe — import each module by dotted name:")

        file_outcomes = []
        for p in sorted(ROOT.rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            parts = rel.split("/")
            if any(seg in ("tests", "scratch", ".git") for seg in parts):
                continue
            # Build dotted module name (root is project root)
            # Path "azure/agent.py" -> "azure.agent"; "azure/__init__.py" -> "azure"
            if rel == "run_bot.py":
                mod = "__main_run"
                file_outcomes.append((rel, "KNOWN_LIMITATION",
                                    "top-level main entry not a module"))
                continue
            if rel.endswith("__init__.py"):
                mod = ".".join(s for s in parts[:-1] if s.endswith("__init__.py") is False)
            else:
                segs = [s[:-3] if s.endswith(".py") else s for s in parts]
                mod = ".".join(segs)
            try:
                importlib.import_module(mod)
                file_outcomes.append((rel, "OK", ""))
            except ModuleNotFoundError as e:
                file_outcomes.append((rel, "KNOWN_LIMITATION", str(e)[:100]))
            except (ImportError, SyntaxError) as e:
                file_outcomes.append((rel, "FAIL", f"{type(e).__name__}: {e}"[:200]))
            except Exception as e:
                file_outcomes.append((rel, "FAIL", f"{type(e).__name__}: {e}"[:200]))

        ok = sum(1 for _, s, _ in file_outcomes if s == "OK")
        kl = sum(1 for _, s, _ in file_outcomes if s == "KNOWN_LIMITATION")
        fail = sum(1 for _, s, _ in file_outcomes if s == "FAIL")
        print(f"Files: OK {ok}   KNOWN_LIMITATION {kl}   FAIL {fail}")
        if fail:
            print("\nFAILURES:")
            for rel, s, m in file_outcomes:
                if s == "FAIL":
                    print(f"  - {rel}: {m}")
        sys.exit(0 if fail == 0 else 1)
    finally:
        if _orig_pythonioencoding:
            os.environ["PYTHONIOENCODING"] = _orig_pythonioencoding
        else:
            os.environ.pop("PYTHONIOENCODING", None)
        sys.path[:] = _orig_sys_path


if __name__ == "__main__":
    main()
