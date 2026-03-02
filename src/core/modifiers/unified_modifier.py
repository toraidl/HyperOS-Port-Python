"""Unified modifier system integrating all plugin types.

This module provides a unified interface for all ROM modifications,
including system-level plugins and APK-level plugins.
"""
from pathlib import Path
from typing import List, Optional

from src.core.modifiers.base_modifier import BaseModifier
from src.core.modifiers.plugin_system import PluginManager
from src.core.modifiers.plugins import (
    WildBoostPlugin,
    EULocalizationPlugin,
    FeatureUnlockPlugin,
    VNDKFixPlugin,
    FileReplacementPlugin,
)
from src.core.modifiers.plugins.apk import ApkModifierPlugin, ApkModifierRegistry
from src.core.config_loader import load_device_config


class UnifiedModifier(BaseModifier):
    """Unified modifier handling both system and APK modifications.
    
    This provides a single entry point for all ROM modifications:
    - System-level: File replacements, wild_boost, features, etc.
    - APK-level: Individual APK patches (installer, settings, etc.)
    """
    
    def __init__(self, context, enable_apk_mods: bool = True):
        super().__init__(context, "UnifiedModifier")
        
        # System-level plugin manager
        self.system_manager = PluginManager(context, self.logger)
        
        # APK-level plugin manager
        self.apk_manager = PluginManager(context, self.logger) if enable_apk_mods else None
        
        self._register_plugins()
    
    def _register_plugins(self):
        """Register all default plugins."""
        # Load device config
        if not hasattr(self.ctx, 'device_config'):
            self.ctx.device_config = load_device_config(
                getattr(self.ctx, 'stock_rom_code', 'unknown'),
                self.logger
            )
        
        # Register system plugins
        self.logger.debug("Registering system-level plugins...")
        self.system_manager.register(FileReplacementPlugin)
        self.system_manager.register(WildBoostPlugin)
        self.system_manager.register(FeatureUnlockPlugin)
        self.system_manager.register(VNDKFixPlugin)
        self.system_manager.register(EULocalizationPlugin)
        
        # Register APK plugins
        if self.apk_manager:
            self.logger.debug("Registering APK-level plugins...")
            ApkModifierRegistry.auto_discover(self.apk_manager)
    
    def run(self, phases: Optional[List[str]] = None) -> bool:
        """Execute all modifications.
        
        Args:
            phases: Optional list of phases to run ('system', 'apk')
                   If None, runs all phases.
        
        Returns:
            bool: True if all phases succeeded
        """
        phases = phases or ['system', 'apk']
        all_success = True
        
        # Phase 1: System-level modifications
        if 'system' in phases:
            self.logger.info("=" * 60)
            self.logger.info("PHASE 1: System-Level Modifications")
            self.logger.info("=" * 60)
            
            results = self.system_manager.execute()
            
            success = sum(1 for r in results.values() if r is True)
            failed = sum(1 for r in results.values() if r is False)
            skipped = sum(1 for r in results.values() if r is None)
            
            self.logger.info(
                f"System modifications: {success} succeeded, "
                f"{failed} failed, {skipped} skipped"
            )
            
            if failed > 0:
                all_success = False
        
        # Phase 2: APK-level modifications
        if 'apk' in phases and self.apk_manager:
            self.logger.info("=" * 60)
            self.logger.info("PHASE 2: APK-Level Modifications")
            self.logger.info("=" * 60)
            
            results = self.apk_manager.execute()
            
            success = sum(1 for r in results.values() if r is True)
            failed = sum(1 for r in results.values() if r is False)
            skipped = sum(1 for r in results.values() if r is None)
            
            self.logger.info(
                f"APK modifications: {success} succeeded, "
                f"{failed} failed, {skipped} skipped"
            )
            
            if failed > 0:
                all_success = False
        
        return all_success
    
    def add_system_plugin(self, plugin_class, **kwargs):
        """Add a custom system-level plugin."""
        self.system_manager.register(plugin_class, **kwargs)
        return self
    
    def add_apk_plugin(self, plugin_class, **kwargs):
        """Add a custom APK-level plugin."""
        if self.apk_manager:
            self.apk_manager.register(plugin_class, **kwargs)
        return self
    
    def enable_system_plugin(self, name: str, enabled: bool = True):
        """Enable/disable a system plugin."""
        self.system_manager.enable_plugin(name, enabled)
        return self
    
    def enable_apk_plugin(self, name: str, enabled: bool = True):
        """Enable/disable an APK plugin."""
        if self.apk_manager:
            self.apk_manager.enable_plugin(name, enabled)
        return self
    
    def list_plugins(self) -> dict:
        """List all registered plugins."""
        return {
            'system': self.system_manager.list_plugins(),
            'apk': self.apk_manager.list_plugins() if self.apk_manager else []
        }


# Backward compatibility: SystemModifier still works as before
class SystemModifier(BaseModifier):
    """Handles system-level ROM modifications using plugins.
    
    Note: This is now a thin wrapper around UnifiedModifier for
    backward compatibility. Consider using UnifiedModifier directly.
    """
    
    def __init__(self, context):
        super().__init__(context, "SystemModifier")
        self._unified = UnifiedModifier(context, enable_apk_mods=False)
    
    def run(self) -> bool:
        """Execute system modifications."""
        return self._unified.run(phases=['system'])
    
    def add_plugin(self, plugin_class, **kwargs):
        """Add a custom plugin."""
        self._unified.add_system_plugin(plugin_class, **kwargs)
        return self
    
    def enable_plugin(self, name: str, enabled: bool = True):
        """Enable/disable a plugin."""
        self._unified.enable_system_plugin(name, enabled)
        return self
    
    def list_plugins(self):
        """List all registered plugins."""
        return self._unified.list_plugins()['system']


class ApkModifier(BaseModifier):
    """Handles APK-level modifications using plugins.
    
    This is a standalone APK modifier that can be used independently
    or as part of UnifiedModifier.
    """
    
    def __init__(self, context):
        super().__init__(context, "ApkModifier")
        self.plugin_manager = PluginManager(context, self.logger)
        self._register_plugins()
    
    def _register_plugins(self):
        """Register APK modification plugins."""
        ApkModifierRegistry.auto_discover(self.plugin_manager)
    
    def run(self) -> bool:
        """Execute all APK modifications."""
        self.logger.info("Starting APK Modifications...")
        
        results = self.plugin_manager.execute()
        
        success = sum(1 for r in results.values() if r is True)
        failed = sum(1 for r in results.values() if r is False)
        skipped = sum(1 for r in results.values() if r is None)
        
        self.logger.info(
            f"APK Modifications Completed: "
            f"{success} succeeded, {failed} failed, {skipped} skipped"
        )
        
        return failed == 0
    
    def add_plugin(self, plugin_class, **kwargs):
        """Add a custom APK plugin."""
        self.plugin_manager.register(plugin_class, **kwargs)
        return self
    
    def enable_plugin(self, name: str, enabled: bool = True):
        """Enable/disable an APK plugin."""
        self.plugin_manager.enable_plugin(name, enabled)
        return self
    
    def list_plugins(self):
        """List all registered APK plugins."""
        return self.plugin_manager.list_plugins()
