import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.core.modifier import SystemModifier
from src.core.context import PortingContext

def test_voice_trigger_fix_android_15(tmp_path):
    # Setup
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    product_dir = target_dir / "product/app/VoiceTrigger"
    product_dir.mkdir(parents=True)
    
    # Create a dummy file to verify move
    (product_dir / "test.apk").touch()
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.base_android_version = "15" # Version < 16
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "14" # Port version (irrelevant for this check but used in init)
    
    modifier = SystemModifier(mock_ctx)
    
    # Run
    modifier._fix_voice_trigger()
    
    # Verify
    # Should be moved to system_ext/app/VoiceTrigger
    system_ext_dir = target_dir / "system_ext/app/VoiceTrigger"
    assert system_ext_dir.exists()
    assert (system_ext_dir / "test.apk").exists()
    assert not product_dir.exists()

def test_voice_trigger_skip_android_16(tmp_path):
    # Setup
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    product_dir = target_dir / "product/app/VoiceTrigger"
    product_dir.mkdir(parents=True)
    (product_dir / "test.apk").touch()
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.base_android_version = "16" # Version >= 16
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "16"

    modifier = SystemModifier(mock_ctx)
    
    # Run
    modifier._fix_voice_trigger()
    
    # Verify
    # Should NOT be moved
    assert product_dir.exists()
    assert not (target_dir / "system_ext/app/VoiceTrigger").exists()

def test_voice_trigger_no_source(tmp_path):
    # Setup
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    product_dir = target_dir / "product/app"
    product_dir.mkdir(parents=True)
    # VoiceTrigger dir DOES NOT exist
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.base_android_version = "15"
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "14"

    modifier = SystemModifier(mock_ctx)
    
    # Run
    modifier._fix_voice_trigger()
    
    # Verify
    # Nothing should happen, no error
    assert not (target_dir / "system_ext/app/VoiceTrigger").exists()

def test_run_calls_voice_trigger_fix(tmp_path):
    """Verify that SystemModifier.run() actually calls _fix_voice_trigger"""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "14"
    
    modifier = SystemModifier(mock_ctx)
    
    # Mock internal methods to avoid side effects
    modifier._process_replacements = MagicMock()
    modifier._migrate_configs = MagicMock()
    modifier._unlock_device_features = MagicMock()
    modifier._fix_vndk_apex = MagicMock()
    modifier._fix_vintf_manifest = MagicMock()
    modifier._fix_voice_trigger = MagicMock()
    modifier._apply_eu_localization = MagicMock()
    
    modifier.run()
    
    modifier._fix_voice_trigger.assert_called_once()
