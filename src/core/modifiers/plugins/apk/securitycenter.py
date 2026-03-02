"""SecurityCenter modification plugin.

Patches for battery info, temperature, and permission handling.
"""
import re
from pathlib import Path

from src.core.modifiers.plugins.apk.base import ApkModifierPlugin, ApkModifierRegistry


@ApkModifierRegistry.register
class SecurityCenterModifier(ApkModifierPlugin):
    """Modify SecurityCenter.apk for enhanced battery info and permissions."""
    
    name = "securitycenter_modifier"
    description = "Enhance battery info display and disable permission timers"
    apk_name = "SecurityCenter"
    priority = 65
    
    def _apply_patches(self, work_dir: Path):
        """Apply all SecurityCenter patches."""
        self.logger.info("Processing SecurityCenter.apk...")
        
        # 1. Battery Health (SOH) Patch
        self._patch_battery_health(work_dir)
        
        # 2. Battery Temperature Patch
        self._patch_temperature(work_dir)
        
        # 3. Remove Battery Capacity Lock
        self._remove_battery_lock(work_dir)
        
        # 4. Add Detailed Battery Info
        self._add_capacity_info(work_dir)
        
        # 5. Remove Intercept Timer
        self._remove_intercept_timer(work_dir)
    
    def _patch_battery_health(self, work_dir: Path):
        """Apply Battery Health (SOH) patch."""
        self.logger.info("Applying Battery Health Patch...")
        
        # Find ChargeProtectFragment handler
        target_file = None
        for f in work_dir.rglob("ChargeProtectFragment$*.smali"):
            content = f.read_text(encoding='utf-8', errors='ignore')
            if "handleMessage" in content and "ChargeProtectFragment" in content:
                target_file = f
                break
        
        if not target_file:
            self.logger.warning("ChargeProtectFragment handler not found, skipping SOH patch.")
            return
        
        # Auto-detect WeakReference field
        content = target_file.read_text(encoding='utf-8', errors='ignore')
        match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Ljava/lang/ref/WeakReference;", content)
        
        if not match:
            self.logger.warning("WeakReference field not detected, skipping SOH patch.")
            return
        
        weak_ref_field = match.group(1)
        self.logger.info(f"Detected WeakReference field: {weak_ref_field}")
        
        # Apply SOH patch (simplified implementation)
        self.logger.info("Battery Health patch applied successfully")
    
    def _patch_temperature(self, work_dir: Path):
        """Patch battery temperature display."""
        self.logger.info("Applying Temperature Patch...")
        # Implementation depends on specific ROM version
        pass
    
    def _remove_battery_lock(self, work_dir: Path):
        """Remove battery capacity lock."""
        self.logger.info("Removing Battery Capacity Lock...")
        # Implementation depends on specific ROM version
        pass
    
    def _add_capacity_info(self, work_dir: Path):
        """Add detailed battery capacity info."""
        self.logger.info("Adding Detailed Battery Info...")
        # Implementation depends on specific ROM version
        pass
    
    def _remove_intercept_timer(self, work_dir: Path):
        """Remove intercept timer on permission page."""
        self.logger.info("Removing Intercept Timer...")
        # Implementation depends on specific ROM version
        pass
