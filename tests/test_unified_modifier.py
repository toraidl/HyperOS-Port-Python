#!/usr/bin/env python3
"""Test script for the unified modifier system.

Verifies that all components work together correctly.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")
    
    # Core modifiers
    from src.core.modifiers import (
        UnifiedModifier,
        SystemModifier,
        ApkModifier,
        FrameworkModifier,
        FirmwareModifier,
    )
    
    # Plugin system
    from src.core.modifiers import (
        ModifierPlugin,
        PluginManager,
        ModifierRegistry,
    )
    
    # System plugins
    from src.core.modifiers import (
        WildBoostPlugin,
        FileReplacementPlugin,
        FeatureUnlockPlugin,
        VNDKFixPlugin,
        EULocalizationPlugin,
    )
    
    # APK plugins
    from src.core.modifiers import (
        ApkModifierPlugin,
        ApkModifierRegistry,
        InstallerModifier,
        SecurityCenterModifier,
        SettingsModifier,
        JoyoseModifier,
        PowerKeeperModifier,
        DevicesOverlayModifier,
    )
    
    print("✓ All imports successful")
    return True


def test_plugin_registration():
    """Test plugin registration."""
    print("\nTesting plugin registration...")
    
    from src.core.modifiers import ApkModifierRegistry, ModifierRegistry
    
    # Check APK modifiers
    apk_mods = ApkModifierRegistry.list_all()
    print(f"  APK modifiers: {len(apk_mods)}")
    for name in apk_mods.keys():
        print(f"    - {name}")
    
    assert len(apk_mods) == 6, f"Expected 6 APK modifiers, got {len(apk_mods)}"
    
    print("✓ Plugin registration working")
    return True


def test_unified_modifier():
    """Test UnifiedModifier creation."""
    print("\nTesting UnifiedModifier...")
    
    from src.core.modifiers import UnifiedModifier
    
    # Create a mock context
    class MockContext:
        target_dir = Path("/tmp/mock_target")
        stock_rom_code = "mock_device"
        device_config = {}
    
    ctx = MockContext()
    
    # Create modifier
    modifier = UnifiedModifier(ctx)
    
    # Check plugins registered
    plugins = modifier.list_plugins()
    system_count = len(plugins['system'])
    apk_count = len(plugins['apk'])
    
    print(f"  System plugins: {system_count}")
    print(f"  APK plugins: {apk_count}")
    
    assert system_count > 0, "No system plugins registered"
    assert apk_count > 0, "No APK plugins registered"
    
    print("✓ UnifiedModifier working")
    return True


def test_plugin_metadata():
    """Test that plugins have correct metadata."""
    print("\nTesting plugin metadata...")
    
    from src.core.modifiers import (
        InstallerModifier, SettingsModifier, WildBoostPlugin
    )
    
    # Test APK modifier
    installer = InstallerModifier
    print(f"  InstallerModifier:")
    print(f"    name: {installer.name}")
    print(f"    apk_name: {installer.apk_name}")
    print(f"    priority: {installer.priority}")
    
    assert installer.name == "installer_modifier"
    assert installer.apk_name == "MIUIPackageInstaller"
    
    # Test system plugin
    wild_boost = WildBoostPlugin
    print(f"  WildBoostPlugin:")
    print(f"    name: {wild_boost.name}")
    print(f"    priority: {wild_boost.priority}")
    
    assert wild_boost.name == "wild_boost"
    
    print("✓ Plugin metadata correct")
    return True


def test_monitoring_integration():
    """Test monitoring integration."""
    print("\nTesting monitoring integration...")
    
    try:
        from src.core.monitoring import Monitor, get_monitor
        from src.core.monitoring.plugin_integration import MonitoredPlugin
        
        monitor = get_monitor()
        monitor.start()
        
        # Create a test plugin
        class TestPlugin(MonitoredPlugin):
            name = "test_plugin"
            priority = 50
            
            def _do_modify(self) -> bool:
                self.record_metric("test_value", 42)
                return True
        
        # Run plugin
        plugin = TestPlugin(None)
        result = plugin.modify()
        
        assert result is True, "Plugin should succeed"
        
        monitor.stop()
        
        print("✓ Monitoring integration working")
        return True
        
    except ImportError:
        print("  Monitoring module not available, skipping")
        return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("UNIFIED MODIFIER SYSTEM TEST")
    print("=" * 60)
    
    tests = [
        ("Imports", test_imports),
        ("Plugin Registration", test_plugin_registration),
        ("Unified Modifier", test_unified_modifier),
        ("Plugin Metadata", test_plugin_metadata),
        ("Monitoring Integration", test_monitoring_integration),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
                print(f"✗ {name} failed")
        except Exception as e:
            failed += 1
            print(f"✗ {name} failed with exception: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
