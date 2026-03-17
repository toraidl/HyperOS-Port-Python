"""Integration of monitoring with modifier plugins.

Connects the monitoring system with the plugin architecture.
"""
import time
from pathlib import Path
from typing import Optional

from src.core.modifiers.plugin_system import ModifierPlugin, PluginManager
from src.core.monitoring import Monitor, get_monitor


class MonitoredPlugin(ModifierPlugin):
    """Base class for plugins with built-in monitoring support.

    Automatically tracks:
    - Execution time
    - Success/failure status
    - Custom metrics
    - Progress updates
    """

    def __init__(self, context, logger=None):
        super().__init__(context, logger)
        self._monitor: Optional[Monitor] = None

    @property
    def monitor(self) -> Monitor:
        """Get or create monitor instance."""
        if self._monitor is None:
            self._monitor = get_monitor()
        return self._monitor

    def check_prerequisites(self) -> bool:
        """Check prerequisites with monitoring."""
        with self.monitor.trace_operation(f"{self.name}.check_prerequisites"):
            result = bool(super().check_prerequisites())
            self.monitor.record_metric(
                f"plugin.{self.name}.prerequisites_passed",
                1 if result else 0,
            )
            return result

    def modify(self) -> bool:
        """Execute modification with full monitoring."""
        self.monitor.increment_counter(f"plugin.{self.name}.attempts")

        with self.monitor.trace_operation(
            f"plugin.{self.name}",
            plugin_name=self.name,
            plugin_version=self.version,
        ) as op:
            try:
                # Update progress
                self.monitor.update_progress(operation=f"Running {self.name}...")

                # Execute actual modification
                result = self._do_modify()

                # Record success
                if result:
                    self.monitor.increment_counter(f"plugin.{self.name}.successes")
                    op.success = True
                else:
                    self.monitor.increment_counter(f"plugin.{self.name}.failures")

                return result

            except Exception as e:
                self.monitor.increment_counter(f"plugin.{self.name}.failures")
                self.monitor.report.add_error(
                    self.name,
                    e,
                    {"plugin_name": self.name, "plugin_version": self.version},
                )
                raise

    def _do_modify(self) -> bool:
        """Override this method instead of modify() in subclasses."""
        raise NotImplementedError("Subclasses must implement _do_modify()")

    def record_metric(self, name: str, value: float, unit: str = ""):
        """Record a plugin-specific metric."""
        self.monitor.record_metric(f"plugin.{self.name}.{name}", value, unit)

    def update_progress(self, step: int, message: str):
        """Update progress within the plugin."""
        self.monitor.update_progress(step, f"{self.name}: {message}")


class MonitoredPluginManager(PluginManager):
    """PluginManager with integrated monitoring.

    Automatically tracks plugin execution and reports metrics.
    """

    def __init__(self, context, logger=None):
        super().__init__(context, logger)
        self._monitor: Optional[Monitor] = None
        self._setup_monitoring_hooks()

    @property
    def monitor(self) -> Monitor:
        """Get or create monitor."""
        if self._monitor is None:
            self._monitor = get_monitor()
        return self._monitor

    def _setup_monitoring_hooks(self):
        """Setup hooks to track plugin execution."""
        # Track when plugins start
        self.add_hook("pre_modify", self._on_plugin_start)

        # Track when plugins complete
        self.add_hook("post_modify", self._on_plugin_complete)

        # Track errors
        self.add_hook("on_error", self._on_plugin_error)

    def _on_plugin_start(self, plugin: ModifierPlugin):
        """Handle plugin start."""
        self.logger.info(f"[Monitor] Plugin starting: {plugin.name}")
        self.monitor.record_metric(f"plugin.{plugin.name}.start_time", time.time())

    def _on_plugin_complete(self, plugin: ModifierPlugin, success: bool):
        """Handle plugin completion."""
        status = "✓" if success else "✗"
        self.logger.info(f"[Monitor] Plugin completed {status}: {plugin.name}")
        self.monitor.record_metric(f"plugin.{plugin.name}.completed", 1 if success else 0)

    def _on_plugin_error(self, plugin: ModifierPlugin, error: Exception):
        """Handle plugin error."""
        self.logger.error(f"[Monitor] Plugin error in {plugin.name}: {error}")
        self.monitor.report.add_error(plugin.name, error)

    def execute(self, plugin_names=None) -> dict[str, bool | None]:
        """Execute plugins with monitoring wrapper."""
        # Track overall execution
        self.monitor.increment_counter("total_plugin_executions")

        with self.monitor.trace_operation("plugin_manager.execute"):
            results = super().execute(plugin_names)

            # Record summary
            success_count = sum(1 for r in results.values() if r is True)
            failure_count = sum(1 for r in results.values() if r is False)

            self.monitor.record_metric("plugin_execution.success", success_count)
            self.monitor.record_metric("plugin_execution.failures", failure_count)

            return results


# Integration helpers

def install_monitoring_hooks(modifier_instance):
    """Install monitoring hooks on a modifier instance.

    This can be used to add monitoring to existing modifiers
    without modifying their code.
    """
    monitor = get_monitor()

    # Store original methods
    if hasattr(modifier_instance, "run"):
        original_run = modifier_instance.run

        def monitored_run(*args, **kwargs):
            modifier_name = modifier_instance.__class__.__name__
            with monitor.phase(modifier_name):
                return original_run(*args, **kwargs)

        modifier_instance.run = monitored_run

    return modifier_instance


def monitored_file_copy(src: Path, dst: Path, monitor: Optional[Monitor] = None) -> bool:
    """Monitored file copy operation."""
    import shutil

    try:
        shutil.copy2(src, dst)
        if monitor:
            monitor.record_metric("files_copied", 1)
            monitor.record_metric("bytes_copied", src.stat().st_size, "bytes")
        return True
    except Exception:
        if monitor:
            monitor.record_metric("copy_errors", 1)
        raise


def monitored_directory_copy(src: Path, dst: Path, monitor: Optional[Monitor] = None) -> bool:
    """Monitored directory copy operation."""
    import shutil

    try:
        total_size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
        file_count = sum(1 for path in src.rglob("*") if path.is_file())

        shutil.copytree(src, dst, dirs_exist_ok=True)

        if monitor:
            monitor.record_metric("directories_copied", 1)
            monitor.record_metric("files_copied", file_count)
            monitor.record_metric("bytes_copied", total_size, "bytes")
        return True
    except Exception:
        if monitor:
            monitor.record_metric("copy_errors", 1)
        raise
