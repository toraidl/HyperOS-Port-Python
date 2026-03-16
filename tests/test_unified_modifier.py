from pathlib import Path

from src.core.modifiers import (
    ApkModifierRegistry,
    EULocalizationPlugin,
    FileReplacementPlugin,
    InstallerModifier,
    PowerKeeperModifier,
    SettingsModifier,
    UnifiedModifier,
    WildBoostPlugin,
)
from src.core.monitoring import get_monitor
from src.core.monitoring.plugin_integration import MonitoredPlugin


def test_modifier_package_exports_expected_symbols():
    assert UnifiedModifier is not None
    assert FileReplacementPlugin.name == "file_replacement"
    assert WildBoostPlugin.name == "wild_boost"
    assert EULocalizationPlugin.name == "eu_localization"


def test_apk_modifier_registry_discovers_expected_modifiers():
    modifier_names = set(ApkModifierRegistry.list_all())

    assert {
        InstallerModifier.apk_name,
        SettingsModifier.apk_name,
        PowerKeeperModifier.apk_name,
    }.issubset(modifier_names)


def test_unified_modifier_lists_system_and_apk_plugins():
    class MockContext:
        target_dir = Path("/tmp/mock_target")
        stock_rom_code = "mock_device"
        device_config = {}

    modifier = UnifiedModifier(MockContext())
    plugins = modifier.list_plugins()

    assert {
        "file_replacement",
        "property_modifier",
        "wild_boost",
        "feature_unlock",
        "vndk_fix",
        "eu_localization",
    }.issubset({plugin.name for plugin in plugins["system"]})
    assert len(plugins["apk"]) >= 6


def test_plugin_metadata_matches_expected_contract():
    assert InstallerModifier.name == "installer_modifier"
    assert InstallerModifier.apk_name == "MIUIPackageInstaller"
    assert WildBoostPlugin.name == "wild_boost"


def test_monitoring_integration_records_metric():
    monitor = get_monitor()
    monitor.start()

    class TestPlugin(MonitoredPlugin):
        name = "test_plugin"
        priority = 50

        def _do_modify(self) -> bool:
            self.record_metric("test_value", 42)
            return True

    plugin = TestPlugin(None)

    assert plugin.modify() is True

    monitor.stop()
