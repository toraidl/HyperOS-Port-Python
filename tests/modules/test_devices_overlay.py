import pytest
from pathlib import Path
from src.modules.devices_overlay import DevicesOverlayModule
from unittest.mock import MagicMock

def test_devices_overlay_patch_android_15(tmp_path):
    """Test patching when Android version is < 16 (e.g. 15)"""
    # Setup
    work_dir = tmp_path / "DevicesAndroidOverlay_15"
    work_dir.mkdir()
    res_dir = work_dir / "res" / "values"
    res_dir.mkdir(parents=True)
    
    xml_file = res_dir / "strings.xml"
    original_content = """<resources>
    <string name="config_dozeComponent">old.service</string>
</resources>"""
    xml_file.write_text(original_content, encoding='utf-8')
    
    # Mock context
    mock_ctx = MagicMock()
    mock_ctx.base_android_version = "15" # Set version to 15
    mock_smali = MagicMock()
    
    module = DevicesOverlayModule(mock_smali, mock_ctx)
    
    # Run
    module.run(work_dir)
    
    # Verify
    new_content = xml_file.read_text(encoding='utf-8')
    assert "com.android.systemui/com.android.keyguard.doze.MiuiDozeService" in new_content
    assert "old.service" not in new_content

def test_devices_overlay_skip_android_16(tmp_path):
    """Test skipping patch when Android version is >= 16"""
    # Setup
    work_dir = tmp_path / "DevicesAndroidOverlay_16"
    work_dir.mkdir()
    res_dir = work_dir / "res" / "values"
    res_dir.mkdir(parents=True)
    
    xml_file = res_dir / "strings.xml"
    original_content = """<resources>
    <string name="config_dozeComponent">old.service</string>
</resources>"""
    xml_file.write_text(original_content, encoding='utf-8')
    
    # Mock context
    mock_ctx = MagicMock()
    mock_ctx.base_android_version = "16" # Set version to 16
    mock_smali = MagicMock()
    
    module = DevicesOverlayModule(mock_smali, mock_ctx)
    
    # Run
    module.run(work_dir)
    
    # Verify content UNCHANGED
    new_content = xml_file.read_text(encoding='utf-8')
    assert "old.service" in new_content
    assert "MiuiDozeService" not in new_content

def test_devices_overlay_skip_android_17(tmp_path):
    """Test skipping patch when Android version is > 16 (e.g. 17)"""
    # Setup
    work_dir = tmp_path / "DevicesAndroidOverlay_17"
    work_dir.mkdir()
    res_dir = work_dir / "res" / "values"
    res_dir.mkdir(parents=True)
    
    xml_file = res_dir / "strings.xml"
    original_content = """<resources>
    <string name="config_dozeComponent">old.service</string>
</resources>"""
    xml_file.write_text(original_content, encoding='utf-8')
    
    # Mock context
    mock_ctx = MagicMock()
    mock_ctx.base_android_version = "17" 
    mock_smali = MagicMock()
    
    module = DevicesOverlayModule(mock_smali, mock_ctx)
    
    # Run
    module.run(work_dir)
    
    # Verify content UNCHANGED
    new_content = xml_file.read_text(encoding='utf-8')
    assert "old.service" in new_content

def test_devices_overlay_patch_invalid_version(tmp_path):
    """Test fallback when version string is invalid (should default to 0 and patch)"""
    # Setup
    work_dir = tmp_path / "DevicesAndroidOverlay_Invalid"
    work_dir.mkdir()
    res_dir = work_dir / "res" / "values"
    res_dir.mkdir(parents=True)
    
    xml_file = res_dir / "strings.xml"
    original_content = """<resources>
    <string name="config_dozeComponent">old.service</string>
</resources>"""
    xml_file.write_text(original_content, encoding='utf-8')
    
    # Mock context
    mock_ctx = MagicMock()
    mock_ctx.base_android_version = "Unknown" 
    mock_smali = MagicMock()
    
    module = DevicesOverlayModule(mock_smali, mock_ctx)
    
    # Run
    module.run(work_dir)
    
    # Verify PATCHED (defaults to version 0 < 16)
    new_content = xml_file.read_text(encoding='utf-8')
    assert "com.android.systemui/com.android.keyguard.doze.MiuiDozeService" in new_content

def test_devices_overlay_no_match(tmp_path):
    # Setup
    work_dir = tmp_path / "DevicesAndroidOverlay_NoMatch"
    work_dir.mkdir()
    res_dir = work_dir / "res" / "values"
    res_dir.mkdir(parents=True)
    
    xml_file = res_dir / "strings.xml"
    original_content = """<resources>
    <string name="other_config">some.value</string>
</resources>"""
    xml_file.write_text(original_content, encoding='utf-8')
    
    mock_ctx = MagicMock()
    mock_ctx.base_android_version = "15" # Ensure we don't skip due to version
    mock_smali = MagicMock()
    
    module = DevicesOverlayModule(mock_smali, mock_ctx)
    
    # Run
    module.run(work_dir)
    
    # Verify content unchanged
    new_content = xml_file.read_text(encoding='utf-8')
    assert new_content == original_content
