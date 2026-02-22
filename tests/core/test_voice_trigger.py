import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.core.modifier import SystemModifier
from src.core.context import PortingContext

def test_voice_trigger_fix_android_15(tmp_path):
    """Test: Android < 16, Stock has VoiceTrigger -> Copy to SystemExt, Del Product"""
    # Setup
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    # Simulate Port's VoiceTrigger (should be removed)
    port_product_vt = target_dir / "product/app/VoiceTrigger"
    port_product_vt.mkdir(parents=True)
    (port_product_vt / "port_app.apk").touch()
    
    # Simulate Stock's VoiceTrigger (should be copied)
    stock_extracted_dir = tmp_path / "stock"
    stock_vt = stock_extracted_dir / "product/app/VoiceTrigger"
    stock_vt.mkdir(parents=True)
    (stock_vt / "stock_app.apk").touch()
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.base_android_version = "15" # Version < 16
    mock_ctx.stock = MagicMock()
    mock_ctx.stock.extracted_dir = stock_extracted_dir
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "14"
    
    modifier = SystemModifier(mock_ctx)
    
    # Run
    modifier._fix_voice_trigger()
    
    # Verify
    # 1. Target SystemExt should contain Stock App
    target_system_ext_vt = target_dir / "system_ext/app/VoiceTrigger"
    assert target_system_ext_vt.exists()
    assert (target_system_ext_vt / "stock_app.apk").exists()
    
    # 2. Target Product should be REMOVED (Port app gone)
    assert not port_product_vt.exists()

def test_voice_trigger_skip_android_16(tmp_path):
    """Test: Android >= 16 -> Do nothing"""
    # Setup
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    port_product_vt = target_dir / "product/app/VoiceTrigger"
    port_product_vt.mkdir(parents=True)
    (port_product_vt / "port_app.apk").touch()
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.base_android_version = "16" # Version >= 16
    mock_ctx.stock = MagicMock()
    mock_ctx.stock.extracted_dir = tmp_path / "stock" # Irrelevant
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "16"

    modifier = SystemModifier(mock_ctx)
    
    # Run
    modifier._fix_voice_trigger()
    
    # Verify
    # Nothing changed
    assert port_product_vt.exists()
    assert not (target_dir / "system_ext/app/VoiceTrigger").exists()

def test_voice_trigger_no_stock_source(tmp_path):
    """Test: Android < 16, but Stock MISSING VoiceTrigger -> Skip"""
    # Setup
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    # Port has it
    port_product_vt = target_dir / "product/app/VoiceTrigger"
    port_product_vt.mkdir(parents=True)
    (port_product_vt / "port_app.apk").touch()
    
    # Stock DOES NOT have it
    stock_extracted_dir = tmp_path / "stock"
    stock_extracted_dir.mkdir()
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.base_android_version = "15"
    mock_ctx.stock = MagicMock()
    mock_ctx.stock.extracted_dir = stock_extracted_dir
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "14"

    modifier = SystemModifier(mock_ctx)
    
    # Run
    modifier._fix_voice_trigger()
    
    # Verify
    # Target Product UNTOUCHED (we don't remove if we can't replace)
    assert port_product_vt.exists()
    # Target SystemExt EMPTY
    assert not (target_dir / "system_ext/app/VoiceTrigger").exists()

def test_run_calls_voice_trigger_fix(tmp_path):
    """Verify integration"""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    
    mock_ctx = MagicMock(spec=PortingContext)
    mock_ctx.target_dir = target_dir
    mock_ctx.port = MagicMock()
    mock_ctx.port.get_prop.return_value = "14"
    
    modifier = SystemModifier(mock_ctx)
    
    # Mock methods
    modifier._process_replacements = MagicMock()
    modifier._migrate_configs = MagicMock()
    modifier._unlock_device_features = MagicMock()
    modifier._fix_vndk_apex = MagicMock()
    modifier._fix_vintf_manifest = MagicMock()
    modifier._fix_voice_trigger = MagicMock()
    modifier._apply_eu_localization = MagicMock()
    
    modifier.run()
    
    modifier._fix_voice_trigger.assert_called_once()
