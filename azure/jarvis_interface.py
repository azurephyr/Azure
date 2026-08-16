"""
JARVIS-Style Interface for Azure

Beautiful terminal interface inspired by Iron Man's JARVIS AI.
Features:
- Animated thinking sequences with status updates
- Live task execution visualization
- System diagnostics display
- Real-time progress tracking
- Professional color-coded output
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("azure.jarvis")


class Colors:
    """JARVIS-style color palette."""
    # JARVIS blue theme
    JARVIS_BLUE = '\033[38;5;39m'      # Bright cyan-blue
    JARVIS_GLOW = '\033[38;5;51m'      # Glowing cyan
    SYSTEM = '\033[38;5;33m'           # System blue

    # Status colors
    SUCCESS = '\033[38;5;46m'          # Bright green
    WARNING = '\033[38;5;226m'         # Bright yellow
    ERROR = '\033[38;5;196m'           # Bright red
    INFO = '\033[38;5;159m'            # Light cyan

    # Text
    WHITE = '\033[97m'
    GRAY = '\033[38;5;245m'
    DIM = '\033[2m'
    BOLD = '\033[1m'

    # Reset
    RESET = '\033[0m'

    @staticmethod
    def glow(text: str) -> str:
        """Make text glow like JARVIS."""
        return f"{Colors.JARVIS_GLOW}{Colors.BOLD}{text}{Colors.RESET}"

    @staticmethod
    def system(text: str) -> str:
        """System message style."""
        return f"{Colors.SYSTEM}{text}{Colors.RESET}"

    @staticmethod
    def success(text: str) -> str:
        """Success message."""
        return f"{Colors.SUCCESS}{text}{Colors.RESET}"

    @staticmethod
    def error(text: str) -> str:
        """Error message."""
        return f"{Colors.ERROR}{text}{Colors.RESET}"


@dataclass
class SystemStatus:
    """Current system status for JARVIS display."""
    thinking: bool = False
    current_task: str = ""
    progress: float = 0.0
    substep: str = ""
    model: str = "Qwen2.5-3B"
    memory_mb: int = 0
    response_time_ms: int = 0


class JarvisInterface:
    """JARVIS-style terminal interface."""

    def __init__(self):
        self.status = SystemStatus()
        self.start_time = None
        self.animation_frame = 0
        self.last_update = 0

    def clear_line(self):
        """Clear current line."""
        sys.stdout.write('\r\033[K')
        sys.stdout.flush()

    def print_header(self):
        """Print JARVIS-style header."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        w = 68
        logger.info(f"\n{Colors.JARVIS_GLOW}+{'-' * w}+{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_GLOW}|{Colors.RESET}  {Colors.glow('Azure Intelligence System')}  " + " " * 37 + f"{Colors.JARVIS_GLOW}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_GLOW}|{Colors.RESET}  {Colors.GRAY}Status: {Colors.SUCCESS}ONLINE{Colors.RESET}  |  {Colors.GRAY}Model: {Colors.INFO}{self.status.model}{Colors.RESET}  |  {Colors.GRAY}{timestamp}{Colors.RESET}  {Colors.JARVIS_GLOW}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_GLOW}+{'-' * w}+{Colors.RESET}\n")


    def thinking_start(self, task: str):
        """Start thinking animation."""
        self.status.thinking = True
        self.status.current_task = task
        self.status.progress = 0.0
        self.start_time = time.time()

        logger.info(f"\n{Colors.JARVIS_BLUE}+{'-' * 67}+{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET} {Colors.glow('[*]')} {Colors.BOLD}{task[:60]}{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}+{'-' * 67}+{Colors.RESET}\n")


    def thinking_update(self, substep: str, progress: float = None):
        """Update thinking animation."""
        if progress is not None:
            self.status.progress = progress
        self.status.substep = substep

        # Animated spinner (ASCII-safe)
        spinner = ['|', '/', '-', '\\']
        self.animation_frame = (self.animation_frame + 1) % len(spinner)

        # Progress bar
        bar_width = 40
        filled = int(bar_width * self.status.progress)
        bar = '#' * filled + '.' * (bar_width - filled)

        # Elapsed time
        elapsed = time.time() - self.start_time if self.start_time else 0

        # Build status line
        self.clear_line()
        status_line = (
            f"{Colors.JARVIS_BLUE}{spinner[self.animation_frame]}{Colors.RESET} "
            f"{Colors.INFO}{substep[:45]}{Colors.RESET} "
            f"{Colors.GRAY}[{Colors.JARVIS_GLOW}{bar}{Colors.GRAY}] "
            f"{int(self.status.progress * 100)}% "
            f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}"
        )

        sys.stdout.write(status_line)
        sys.stdout.flush()
        time.sleep(0.05)  # Smooth animation

    def thinking_complete(self, result: str = "Complete"):
        """Complete thinking animation."""
        self.status.thinking = False
        elapsed = time.time() - self.start_time if self.start_time else 0

        self.clear_line()
        logger.info(f"{Colors.SUCCESS}[OK]{Colors.RESET} {Colors.BOLD}{result}{Colors.RESET} {Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")


    def show_task_execution(self, task_name: str, steps: list[str]):
        """Show live task execution."""
        logger.info(f"\n{Colors.JARVIS_BLUE}+{'-' * 67}+{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET} {Colors.glow('[>]')} {Colors.BOLD}EXECUTING: {task_name}{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}+{'-' * 67}+{Colors.RESET}\n")


        for i, step in enumerate(steps, 1):
            # Animate each step
            sys.stdout.write(f"{Colors.GRAY}  [{i}/{len(steps)}]{Colors.RESET} {step}... ")
            sys.stdout.flush()

            # Simulated processing (replace with actual execution)
            time.sleep(0.1)

            logger.info(f"{Colors.SUCCESS}[OK]{Colors.RESET}")


        logger.info(f"\n{Colors.SUCCESS}[OK] Task completed successfully{Colors.RESET}\n")


    def show_system_diagnostics(self):
        """Show system diagnostics like JARVIS."""
        w = 63
        logger.info(f"\n{Colors.JARVIS_BLUE}+{'=' * w}+{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET}  {Colors.glow('SYSTEM DIAGNOSTICS')}                                             {Colors.JARVIS_BLUE}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{'-' * w}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET}  {Colors.INFO}[*]{Colors.RESET} Neural Network:  {Colors.SUCCESS}OPERATIONAL{Colors.RESET}                              {Colors.JARVIS_BLUE}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET}  {Colors.INFO}[*]{Colors.RESET} Cognitive Pipeline: {Colors.SUCCESS}ACTIVE{Colors.RESET} (10-phase reasoning)         {Colors.JARVIS_BLUE}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET}  {Colors.INFO}[*]{Colors.RESET} Memory Systems:  {Colors.SUCCESS}ONLINE{Colors.RESET} (Short-term + Long-term + RAG)   {Colors.JARVIS_BLUE}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET}  {Colors.INFO}[*]{Colors.RESET} Model: {Colors.INFO}Qwen2.5-3B-Instruct{Colors.RESET} (Quantized Q4_K_M)          {Colors.JARVIS_BLUE}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET}  {Colors.INFO}[*]{Colors.RESET} Response Time:   {Colors.INFO}~2.3s average{Colors.RESET}                           {Colors.JARVIS_BLUE}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}|{Colors.RESET}  {Colors.INFO}[*]{Colors.RESET} Memory Usage:    {Colors.INFO}2.5 GB{Colors.RESET}                                   {Colors.JARVIS_BLUE}|{Colors.RESET}")

        logger.info(f"{Colors.JARVIS_BLUE}+{'=' * w}+{Colors.RESET}\n")


    def log_message(self, channel: str, user: str, message: str, is_bot: bool = False):
        """Log a Discord message in JARVIS style."""
        timestamp = datetime.now().strftime("%H:%M:%S")

        if is_bot:
            icon = f"{Colors.JARVIS_GLOW}[*]{Colors.RESET}"
            user_color = Colors.JARVIS_BLUE
            prefix = "ADAM"
        else:
            icon = f"{Colors.INFO}[o]{Colors.RESET}"
            user_color = Colors.WHITE
            prefix = user

        logger.info(f"{Colors.DIM}[{timestamp}]{Colors.RESET} {Colors.GRAY}{channel}{Colors.RESET}")

        logger.info(f"  {icon} {user_color}{prefix}:{Colors.RESET} {message[:80]}")


    def log_action(self, action: str, target: str, success: bool = True):
        """Log a bot action."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        status = Colors.success("[OK]") if success else Colors.error("[FAIL]")

        logger.info(f"{Colors.DIM}[{timestamp}]{Colors.RESET} {status} {Colors.BOLD}{action}{Colors.RESET}: {Colors.INFO}{target}{Colors.RESET}")


    def log_error(self, error: str):
        """Log an error."""
        logger.error(f"\n{Colors.ERROR}[FAIL] ERROR:{Colors.RESET} {error}\n")


    def log_warning(self, warning: str):
        """Log a warning."""
        logger.warning(f"\n{Colors.WARNING}[WARN] WARNING:{Colors.RESET} {warning}\n")



# Global instance
jarvis = JarvisInterface()


def demo():
    """Demonstration of JARVIS interface."""
    jarvis.print_header()
    jarvis.show_system_diagnostics()

    # Simulate thinking
    jarvis.thinking_start("Analyzing user request")
    for i in range(10):
        substeps = [
            "Loading context...",
            "Parsing intent...",
            "Accessing memory systems...",
            "Running cognitive pipeline...",
            "Analyzing sentiment...",
            "Evaluating risk...",
            "Planning response...",
            "Generating output...",
            "Validating safety...",
            "Finalizing response...",
        ]
        jarvis.thinking_update(substeps[i], (i + 1) / 10)
        time.sleep(0.3)
    jarvis.thinking_complete("Response generated")

    # Simulate task execution
    jarvis.show_task_execution(
        "Server Setup",
        [
            "Creating Admin role (red)",
            "Creating Moderator role (blue)",
            "Creating Information category",
            "Creating #rules channel",
            "Setting permissions",
        ]
    )

    # Simulate messages
    jarvis.log_message("#general", "User", "hey azure, how are you?")
    jarvis.log_message("#general", "Azure", "I'm operating at peak efficiency. How may I assist you?", is_bot=True)

    # Simulate actions
    jarvis.log_action("Ban", "@Spammer", success=True)
    jarvis.log_action("Create Role", "Admin", success=True)


if __name__ == "__main__":
    demo()
