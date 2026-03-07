"""Console UI and progress visualization for monitoring.

Provides real-time progress bars, spinners, and console output.
"""

import sys
import time
import threading
import logging
from typing import Optional, List
from dataclasses import dataclass, field


# Logger for diagnostic messages (not for UI output)
logger = logging.getLogger(__name__)


@dataclass
class ConsoleStyle:
    """Console styling options."""

    success: str = "✓"
    error: str = "✗"
    warning: str = "⚠"
    info: str = "ℹ"
    progress: str = "▓"
    empty: str = "░"
    spinner: List[str] = field(
        default_factory=lambda: ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    )


class ProgressBar:
    """Animated progress bar for console output."""

    def __init__(self, total: int = 100, width: int = 40, style: Optional[ConsoleStyle] = None):
        self.total = total
        self.width = width
        self.style = style or ConsoleStyle()
        self.current = 0
        self.message = ""
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self, message: str = ""):
        """Start the progress bar."""
        self.message = message
        self._active = True
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def update(self, current: int, message: Optional[str] = None):
        """Update progress."""
        with self._lock:
            self.current = min(int(current), self.total)
            if message:
                self.message = message

    def finish(self, message: Optional[str] = None):
        """Finish and clear the progress bar."""
        self._active = False
        if self._thread:
            self._thread.join(timeout=0.5)

        # Clear line
        sys.stdout.write("\r" + " " * (self.width + 50) + "\r")
        if message:
            sys.stdout.write(message + "\n")
        sys.stdout.flush()

    def _animate(self):
        """Animation loop."""
        while self._active:
            with self._lock:
                progress = self.current / self.total if self.total > 0 else 0
                filled = int(self.width * progress)
                bar = self.style.progress * filled + self.style.empty * (self.width - filled)
                percent = progress * 100

                line = f"\r[{bar}] {percent:5.1f}% {self.message}"
                sys.stdout.write(line)
                sys.stdout.flush()

            time.sleep(0.1)


class Spinner:
    """Animated spinner for indeterminate progress."""

    def __init__(self, message: str = "Working...", style: Optional[ConsoleStyle] = None):
        self.message = message
        self.style = style or ConsoleStyle()
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._frame = 0

    def start(self):
        """Start the spinner."""
        self._active = True
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def stop(self, message: Optional[str] = None):
        """Stop the spinner."""
        self._active = False
        if self._thread:
            self._thread.join(timeout=0.5)

        # Clear line
        sys.stdout.write("\r" + " " * (len(self.message) + 10) + "\r")
        if message:
            sys.stdout.write(message + "\n")
        sys.stdout.flush()

    def _animate(self):
        """Animation loop."""
        while self._active:
            char = self.style.spinner[self._frame % len(self.style.spinner)]
            sys.stdout.write(f"\r{char} {self.message}")
            sys.stdout.flush()
            self._frame += 1
            time.sleep(0.1)


class ConsoleReporter:
    """Reports monitoring data to console in real-time."""

    def __init__(self, style: Optional[ConsoleStyle] = None):
        self.style = style or ConsoleStyle()
        self._progress_bar: Optional[ProgressBar] = None
        self._spinner: Optional[Spinner] = None
        self._phase_stack: List[str] = []

    def on_phase_start(self, phase: str):
        """Called when a phase starts."""
        self._phase_stack.append(phase)
        indent = "  " * (len(self._phase_stack) - 1)
        # UI output: print to console for user visibility
        print(f"{indent}{self.style.info} Starting: {phase}")

    def on_phase_end(self, phase: str, success: bool, duration: float):
        """Called when a phase ends."""
        if self._phase_stack and self._phase_stack[-1] == phase:
            self._phase_stack.pop()

        indent = "  " * len(self._phase_stack)
        icon = self.style.success if success else self.style.error
        # UI output: print completion status to console
        print(f"{indent}{icon} {phase}: {duration:.2f}s")

    def on_progress_update(self, current: int, total: int, operation: str):
        """Called on progress update."""
        if self._progress_bar is None:
            self._progress_bar = ProgressBar(total=total)
            self._progress_bar.start(operation)
        else:
            self._progress_bar.update(current, operation)

        if current >= total:
            self._progress_bar.finish(f"{self.style.success} {operation}")
            self._progress_bar = None

    def on_operation_start(self, name: str):
        """Called when an operation starts."""
        if self._spinner is None:
            self._spinner = Spinner(f"{name}...")
            self._spinner.start()

    def on_operation_end(self, name: str, success: bool):
        """Called when an operation ends."""
        if self._spinner:
            icon = self.style.success if success else self.style.error
            self._spinner.stop(f"{icon} {name}")
            self._spinner = None

    def on_error(self, phase: str, error: str):
        """Called when an error occurs."""
        # Log error for diagnostics
        logger.error("Error in %s: %s", phase, error)
        # UI output: display error to user
        print(f"{self.style.error} Error in {phase}: {error}")

    def on_metric(self, name: str, value: float, unit: str = ""):
        """Called when a metric is recorded."""
        # Only show important metrics
        if name in ["files_processed", "errors_count", "duration"]:
            # UI output: display metric to user
            print(f"  {self.style.info} {name}: {value}{unit}")


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def format_bytes(bytes_val: float) -> str:
    """Format bytes in human-readable format."""
    value = float(bytes_val)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def print_table(headers: List[str], rows: List[List[str]], padding: int = 2):
    """Print a formatted table to console."""
    # Calculate column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    # Print header
    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, widths))
    # UI output: table header
    print(header_line)
    # UI output: table separator
    print("-" * len(header_line))

    # Print rows
    for row in rows:
        # UI output: table row
        print(" | ".join(str(cell).ljust(w) for cell, w in zip(row, widths)))
