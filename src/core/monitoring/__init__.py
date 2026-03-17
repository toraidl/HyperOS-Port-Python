"""Advanced monitoring and logging system for ROM modifications.

This module provides comprehensive monitoring capabilities including:
- Structured logging with context
- Performance metrics collection
- Execution tracing
- Progress reporting
- Resource usage monitoring
"""

import functools
import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class MetricPoint:
    """A single metric data point."""

    timestamp: float
    name: str
    value: float
    unit: str = ""
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class OperationRecord:
    """Record of a single operation execution."""

    name: str
    start_time: float
    end_time: float = 0.0
    success: bool = False
    error_message: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    sub_operations: List["OperationRecord"] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """Calculate operation duration in seconds."""
        if self.end_time == 0.0:
            return time.time() - self.start_time
        return self.end_time - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "success": self.success,
            "error_message": self.error_message,
            "metrics": self.metrics,
            "sub_operations": [op.to_dict() for op in self.sub_operations],
        }


class MetricsCollector:
    """Collects and manages performance metrics."""

    def __init__(self):
        self._metrics: List[MetricPoint] = []
        self._counters: Dict[str, float] = {}
        self._gauges: Dict[str, float] = {}
        self._lock = threading.Lock()

    def record(self, name: str, value: float, unit: str = "", **tags):
        """Record a metric value."""
        with self._lock:
            self._metrics.append(
                MetricPoint(timestamp=time.time(), name=name, value=value, unit=unit, tags=tags)
            )

    def increment(self, name: str, value: float = 1.0):
        """Increment a counter."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def gauge(self, name: str, value: float):
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = value

    def get_counter(self, name: str) -> float:
        """Get counter value."""
        with self._lock:
            return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        """Get gauge value."""
        with self._lock:
            return self._gauges.get(name, 0.0)

    def get_metrics(self, name: Optional[str] = None) -> List[MetricPoint]:
        """Get all metrics or metrics matching name."""
        with self._lock:
            if name is None:
                return list(self._metrics)
            return [m for m in self._metrics if m.name == name]

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "total_metrics": len(self._metrics),
                "metric_names": list(set(m.name for m in self._metrics)),
            }

    def clear(self):
        """Clear all metrics."""
        with self._lock:
            self._metrics.clear()
            self._counters.clear()
            self._gauges.clear()


class ExecutionTracer:
    """Traces execution of operations and builds call trees."""

    def __init__(self):
        self._root_operations: List[OperationRecord] = []
        self._operation_stack: List[OperationRecord] = []
        self._lock = threading.Lock()

    @contextmanager
    def trace(self, name: str, **metadata):
        """Context manager for tracing an operation.

        Usage:
            with tracer.trace("my_operation", category="file_ops"):
                # Do work
                pass
        """
        record = OperationRecord(name=name, start_time=time.time(), metrics=metadata)

        with self._lock:
            if self._operation_stack:
                # Add as sub-operation
                self._operation_stack[-1].sub_operations.append(record)
            else:
                # Add as root operation
                self._root_operations.append(record)

            self._operation_stack.append(record)

        try:
            yield record
            record.success = True
        except Exception as e:
            record.success = False
            record.error_message = str(e)
            raise
        finally:
            record.end_time = time.time()
            with self._lock:
                if self._operation_stack and self._operation_stack[-1] is record:
                    self._operation_stack.pop()

    def get_operations(self) -> List[OperationRecord]:
        """Get all root operations."""
        with self._lock:
            return list(self._root_operations)

    def get_summary(self) -> Dict[str, Any]:
        """Get execution summary."""
        with self._lock:
            total_ops = len(self._root_operations)
            successful_ops = sum(1 for op in self._root_operations if op.success)
            total_duration = sum(op.duration for op in self._root_operations)

            return {
                "total_operations": total_ops,
                "successful_operations": successful_ops,
                "failed_operations": total_ops - successful_ops,
                "total_duration": total_duration,
                "average_duration": total_duration / total_ops if total_ops > 0 else 0,
            }

    def to_dict(self) -> Dict[str, Any]:
        """Convert trace to dictionary."""
        with self._lock:
            return {
                "operations": [op.to_dict() for op in self._root_operations],
                "summary": self.get_summary(),
            }

    def clear(self):
        """Clear all traces."""
        with self._lock:
            self._root_operations.clear()
            self._operation_stack.clear()


class ProgressTracker:
    """Tracks progress of long-running operations."""

    def __init__(self, total_steps: int = 100):
        self.total_steps = total_steps
        self.current_step = 0
        self.current_operation = ""
        self._listeners: List[Callable[[int, int, str], None]] = []
        self._lock = threading.Lock()
        self._start_time = time.time()

    def add_listener(self, callback: Callable[[int, int, str], None]):
        """Add a progress listener callback.

        Callback receives: (current_step, total_steps, current_operation)
        """
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[int, int, str], None]):
        """Remove a progress listener."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def update(self, step: Optional[int] = None, operation: Optional[str] = None):
        """Update progress."""
        with self._lock:
            if step is not None:
                self.current_step = min(step, self.total_steps)
            if operation is not None:
                self.current_operation = operation

            # Notify listeners
            for listener in list(self._listeners):
                try:
                    listener(self.current_step, self.total_steps, self.current_operation)
                except Exception:
                    pass

    def advance(self, steps: int = 1, operation: Optional[str] = None):
        """Advance progress by N steps."""
        self.update(self.current_step + steps, operation)

    @property
    def percentage(self) -> float:
        """Get progress percentage."""
        return (self.current_step / self.total_steps) * 100 if self.total_steps > 0 else 0

    @property
    def estimated_time_remaining(self) -> float:
        """Estimate remaining time in seconds."""
        if self.current_step == 0:
            return 0
        elapsed = time.time() - self._start_time
        rate = elapsed / self.current_step
        return rate * (self.total_steps - self.current_step)


class MonitoringReport:
    """Comprehensive monitoring report."""

    def __init__(self):
        self.start_time: datetime = datetime.now()
        self.end_time: Optional[datetime] = None
        self.metrics_collector = MetricsCollector()
        self.execution_tracer = ExecutionTracer()
        self.phase_results: Dict[str, Dict[str, Any]] = {}
        self.errors: List[Dict[str, Any]] = []

    def add_phase_result(self, phase: str, success: bool, details: Dict[str, Any]):
        """Add a phase execution result."""
        self.phase_results[phase] = {
            "success": success,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        }

    def add_error(self, phase: str, error: Exception, context: Optional[Dict] = None):
        """Record an error with context."""
        self.errors.append(
            {
                "phase": phase,
                "type": type(error).__name__,
                "message": str(error),
                "timestamp": datetime.now().isoformat(),
                "context": context or {},
            }
        )

    def finalize(self):
        """Finalize the report."""
        self.end_time = datetime.now()

    def generate(self) -> Dict[str, Any]:
        """Generate the complete report."""
        duration = 0.0
        if self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()

        return {
            "report_type": "rom_modification_monitoring",
            "version": "1.0",
            "timestamp": self.start_time.isoformat(),
            "duration_seconds": duration,
            "summary": {
                "phases_completed": sum(1 for p in self.phase_results.values() if p["success"]),
                "phases_failed": sum(1 for p in self.phase_results.values() if not p["success"]),
                "total_errors": len(self.errors),
            },
            "metrics": self.metrics_collector.get_summary(),
            "execution_trace": self.execution_tracer.to_dict(),
            "phase_results": self.phase_results,
            "errors": self.errors,
        }

    def save(self, path: Path):
        """Save report to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.generate(), f, indent=2)

    def print_summary(self):
        """Print human-readable summary to console.

        Note: This method intentionally uses print() for direct console output
        as it is a report display function meant for user-facing output.
        """
        report = self.generate()

        print("\n" + "=" * 60)
        print("ROM MODIFICATION REPORT")
        print("=" * 60)
        print(f"Duration: {report['duration_seconds']:.2f} seconds")
        print(
            f"Phases: {report['summary']['phases_completed']} completed, "
            f"{report['summary']['phases_failed']} failed"
        )
        print(f"Total Errors: {report['summary']['total_errors']}")

        if report["errors"]:
            print("\nErrors:")
            for error in report["errors"]:
                print(f"  [{error['phase']}] {error['type']}: {error['message']}")

        print("\nMetrics Summary:")
        for name, value in report["metrics"]["counters"].items():
            print(f"  {name}: {value}")

        print("=" * 60 + "\n")


class Monitor:
    """Main monitoring interface that coordinates all monitoring components."""

    def __init__(self):
        self.report = MonitoringReport()
        self.progress = ProgressTracker()
        self.logger = logging.getLogger("Monitor")
        self._active = False

    def start(self):
        """Start monitoring."""
        self._active = True
        self.report.start_time = datetime.now()
        self.logger.info("Monitoring started")

    def stop(self):
        """Stop monitoring."""
        self._active = False
        self.report.finalize()
        self.logger.info("Monitoring stopped")

    @contextmanager
    def phase(self, name: str):
        """Context manager for monitoring a phase.

        Usage:
            with monitor.phase("extraction"):
                # Phase work
                pass
        """
        self.logger.info(f"Phase started: {name}")
        with self.report.execution_tracer.trace(name) as record:
            try:
                yield self
                self.report.add_phase_result(name, True, {"duration": record.duration})
                self.logger.info(f"Phase completed: {name} ({record.duration:.2f}s)")
            except Exception as e:
                self.report.add_phase_result(
                    name, False, {"duration": record.duration, "error": str(e)}
                )
                self.report.add_error(name, e)
                self.logger.error(f"Phase failed: {name} - {e}")
                raise

    def record_metric(self, name: str, value: float, unit: str = "", **tags):
        """Record a metric."""
        if not self._active:
            return
        self.report.metrics_collector.record(name, value, unit, **tags)
        self.logger.debug(f"Metric: {name}={value}{unit}")

    def increment_counter(self, name: str, value: float = 1.0):
        """Increment a counter."""
        if not self._active:
            return
        self.report.metrics_collector.increment(name, value)

    def trace_operation(self, name: str, **metadata):
        """Get a context manager for tracing an operation."""
        return self.report.execution_tracer.trace(name, **metadata)

    def update_progress(self, step: Optional[int] = None, operation: Optional[str] = None):
        """Update progress."""
        if not self._active:
            return
        self.progress.update(step, operation)

    def add_progress_listener(self, callback: Callable[[int, int, str], None]):
        """Add a progress listener."""
        self.progress.add_listener(callback)

    def save_report(self, path: Path):
        """Save monitoring report."""
        self.report.save(path)

    def print_report(self):
        """Print monitoring report."""
        self.report.print_summary()


# Decorator for monitoring functions
def monitored(name: Optional[str] = None, track_metrics: bool = True):
    """Decorator to monitor function execution.

    Usage:
        @monitored("my_operation")
        def my_function():
            pass
    """

    def decorator(func: Callable) -> Callable:
        operation_name = name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Try to get monitor from context
            monitor = None
            if args and hasattr(args[0], "_monitor"):
                monitor = args[0]._monitor
            elif "monitor" in kwargs:
                monitor = kwargs["monitor"]

            if monitor and track_metrics:
                with monitor.trace_operation(operation_name):
                    return func(*args, **kwargs)
            else:
                return func(*args, **kwargs)

        return wrapper

    return decorator


# Global monitor instance
_global_monitor: Optional[Monitor] = None


def get_monitor() -> Monitor:
    """Get or create global monitor instance."""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = Monitor()
    return _global_monitor


def set_monitor(monitor: Monitor):
    """Set global monitor instance."""
    global _global_monitor
    _global_monitor = monitor


def reset_monitor():
    """Reset global monitor."""
    global _global_monitor
    _global_monitor = None
