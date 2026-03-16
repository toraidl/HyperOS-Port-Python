from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.modifiers import SystemModifier


def test_system_modifier_loads_device_config_when_missing():
    context = SimpleNamespace(stock_rom_code="fuxi")

    with patch("src.core.modifiers.system_modifier.load_device_config", return_value={}) as loader:
        modifier = SystemModifier(context)

    loader.assert_called_once_with("fuxi", modifier.logger)
    assert context.device_config == {}


def test_system_modifier_registers_default_plugins_in_priority_order():
    context = SimpleNamespace(device_config={})

    modifier = SystemModifier(context)

    assert [plugin.name for plugin in modifier.list_plugins()] == [
        "file_replacement",
        "wild_boost",
        "feature_unlock",
        "vndk_fix",
        "eu_localization",
    ]


def test_system_modifier_run_returns_true_when_all_plugins_succeed():
    context = SimpleNamespace(device_config={})
    modifier = SystemModifier(context)
    modifier.plugin_manager.execute = MagicMock(return_value={"a": True, "b": None})

    assert modifier.run() is True


def test_system_modifier_run_returns_false_when_any_plugin_fails():
    context = SimpleNamespace(device_config={})
    modifier = SystemModifier(context)
    modifier.plugin_manager.execute = MagicMock(return_value={"a": True, "b": False})

    assert modifier.run() is False
