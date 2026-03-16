"""Unit tests for ConfigMerger class."""

import json
import logging

import pytest

from src.core.config_loader import ConfigMerger, load_device_config


class TestConfigMerger:
    """Test cases for ConfigMerger class."""

    @pytest.fixture
    def merger(self, tmp_path):
        """Create a ConfigMerger instance with temporary directories."""
        logger = logging.getLogger("test")
        return ConfigMerger(logger)

    def test_deep_merge_basic(self, merger):
        """Test basic deep merge functionality."""
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        override = {"b": {"c": 10}, "e": 5}
        result = merger.deep_merge(base, override)

        assert result["a"] == 1
        assert result["b"]["c"] == 10
        assert result["b"]["d"] == 3
        assert result["e"] == 5

    def test_deep_merge_nested(self, merger):
        """Test deep merge with nested dictionaries."""
        base = {"level1": {"level2": {"value": "original"}}}
        override = {"level1": {"level2": {"value": "overridden"}}}
        result = merger.deep_merge(base, override)
        assert result["level1"]["level2"]["value"] == "overridden"

    def test_deep_merge_skips_metadata(self, merger):
        """Test that metadata keys (starting with _) are skipped."""
        base = {"a": 1}
        override = {"_comment": "This should be ignored", "a": 2}
        result = merger.deep_merge(base, override)

        assert "_comment" not in result
        assert result["a"] == 2

    def test_load_config_file_not_found(self, merger, tmp_path):
        """Test loading a non-existent config file."""
        config_path = tmp_path / "nonexistent.json"
        result = merger.load_config(config_path)
        assert result == {}

    def test_load_config_valid_json(self, merger, tmp_path):
        """Test loading a valid JSON config file."""
        config_path = tmp_path / "config.json"
        config_data = {"key": "value", "nested": {"inner": "data"}}

        with open(config_path, "w") as f:
            json.dump(config_data, f)

        result = merger.load_config(config_path)
        assert result == config_data

    def test_load_config_invalid_json(self, merger, tmp_path, caplog):
        """Test loading an invalid JSON file logs error."""
        config_path = tmp_path / "invalid.json"
        config_path.write_text("not valid json")

        with caplog.at_level(logging.ERROR):
            result = merger.load_config(config_path)

        assert result == {}
        assert "Failed to parse" in caplog.text

    def test_load_config_io_error(self, merger, tmp_path, caplog):
        """Test handling IO errors during config loading."""
        # Create a directory with the same name to cause read error
        config_path = tmp_path / "config.json"
        config_path.mkdir()

        with caplog.at_level(logging.ERROR):
            result = merger.load_config(config_path)

        assert result == {}


class TestLoadDeviceConfig:
    """Test cases for load_device_config function."""

    def test_load_device_config_nonexistent_device(self, tmp_path, monkeypatch, caplog):
        """Test loading config for a non-existent device."""
        monkeypatch.chdir(tmp_path)

        # Create devices directory structure
        devices_dir = tmp_path / "devices"
        devices_dir.mkdir()
        (devices_dir / "common").mkdir()
        (devices_dir / "common" / "config.json").write_text('{"common_key": "value"}')

        with caplog.at_level(logging.INFO):
            result = load_device_config("nonexistent")

        assert "common_key" in result

    def test_load_device_config_merges_configs(self, tmp_path, monkeypatch):
        """Test that device config properly merges with common config."""
        monkeypatch.chdir(tmp_path)

        devices_dir = tmp_path / "devices"
        common_dir = devices_dir / "common"
        device_dir = devices_dir / "testdevice"
        common_dir.mkdir(parents=True)
        device_dir.mkdir(parents=True)

        # Write common config
        with open(common_dir / "config.json", "w") as f:
            json.dump({"wild_boost": {"enable": False}, "pack": {"type": "payload"}}, f)

        # Write device config
        with open(device_dir / "config.json", "w") as f:
            json.dump({"wild_boost": {"enable": True}}, f)

        result = load_device_config("testdevice")

        # Device should override common
        assert result["wild_boost"]["enable"] is True
        # Common values should be preserved
        assert result["pack"]["type"] == "payload"
