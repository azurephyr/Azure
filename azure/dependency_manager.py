"""
Azure Dependency Bootstrapper

Auto-detects and installs missing Python packages for v3 modules.
Safe, non-blocking, and idempotent.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger("azure.dependency_manager")


class DependencyManager:
    """
    Manages optional dependencies for Azure v3 modules.

    Usage:
        dm = DependencyManager()
        dm.ensure("vision")  # Installs transformers, Pillow if needed
        dm.ensure_all()      # Installs everything available
    """

    # Module → required packages mapping
    DEPENDENCIES = {
        "vision": {
            "packages": ["transformers", "Pillow", "requests"],
            "optional_models": ["Salesforce/blip-image-captioning-base"],
            "description": "Image understanding, captioning, and OCR",
        },
        "voice": {
            "packages": ["pyttsx3", "gTTS", "SpeechRecognition"],
            "optional_models": [],
            "description": "Text-to-speech and speech-to-text",
        },
        "documents": {
            "packages": ["PyPDF2", "python-docx", "openpyxl"],
            "optional_models": [],
            "description": "PDF, Word, and Excel parsing",
        },
        "rag": {
            "packages": ["sentence-transformers"],
            "optional_models": ["all-MiniLM-L6-v2"],
            "description": "Dense vector embeddings for RAG",
        },
        "integrations": {
            "packages": ["requests"],
            "optional_models": [],
            "description": "External API integrations (GitHub, Notion, etc.)",
        },
    }

    def __init__(self):
        self._installed: dict[str, bool] = {}
        self._checked: set = set()

    def is_available(self, module: str) -> bool:
        """Check if a module's dependencies are available."""
        if module in self._installed:
            return self._installed[module]
        deps = self.DEPENDENCIES.get(module, {})
        packages = deps.get("packages", [])
        for pkg in packages:
            pkg_name = pkg.strip().lower().split("==")[0].split(">=")[0]
            if not self._check_import(pkg_name):
                self._installed[module] = False
                return False
        self._installed[module] = True
        return True

    def ensure(self, module: str, auto_install: bool = True) -> bool:
        """
        Ensure a module's dependencies are installed.
        Returns True if ready to use.
        """
        if self.is_available(module):
            return True
        if not auto_install:
            return False

        deps = self.DEPENDENCIES.get(module, {})
        packages = deps.get("packages", [])
        logger.info(f"[dependency_manager] Installing dependencies for '{module}': {packages}")


        for pkg in packages:
            pkg_name = pkg.strip().lower().split("==")[0].split(">=")[0]
            if self._check_import(pkg_name):
                continue
            try:
                self._install_package(pkg)
            except Exception as e:
                logger.error(f"[dependency_manager] Failed to install {pkg}: {e}")

                return False

        # Re-check
        self._installed[module] = all(
            self._check_import(p.strip().lower().split("==")[0].split(">=")[0])
            for p in packages
        )

        if self._installed[module]:
            logger.info(f"[dependency_manager] '{module}' dependencies ready")

        else:
            logger.info(f"[dependency_manager] '{module}' dependencies incomplete")

        return self._installed[module]

    def ensure_all(self, auto_install: bool = True) -> dict[str, bool]:
        """Ensure all module dependencies. Returns status map."""
        return {m: self.ensure(m, auto_install) for m in self.DEPENDENCIES}

    def get_status(self) -> dict[str, dict]:
        """Get full status of all modules."""
        return {
            m: {
                "available": self.is_available(m),
                "description": info["description"],
                "packages": info["packages"],
            }
            for m, info in self.DEPENDENCIES.items()
        }

    def get_help_text(self) -> str:
        """Get human-readable dependency status."""
        lines = ["**Azure Module Dependencies:**"]
        for m, info in self.DEPENDENCIES.items():
            status = "🟢" if self.is_available(m) else "🔴"
            lines.append(f"{status} **{m}**: {info['description']}")
            lines.append(f"   Packages: {', '.join(info['packages'])}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_import(self, package_name: str) -> bool:
        """Check if a package can be imported."""
        if package_name in self._checked:
            return True
        # Map common package names to import names
        import_map = {
            "pillow": "PIL",
            "python-docx": "docx",
            "speechrecognition": "speech_recognition",
        }
        import_name = import_map.get(package_name, package_name)
        try:
            __import__(import_name)
            self._checked.add(package_name)
            return True
        except ImportError:
            return False

    def _install_package(self, package: str) -> bool:
        """Install a package via pip."""
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", package]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"pip install failed: {result.stderr}")
        return True


# Global singleton
DEPENDENCY_MANAGER = DependencyManager()


def ensure_dependencies(module: str, auto_install: bool = True) -> bool:
    """Convenience function."""
    return DEPENDENCY_MANAGER.ensure(module, auto_install)


def get_dependency_status() -> dict[str, dict]:
    """Convenience function."""
    return DEPENDENCY_MANAGER.get_status()
