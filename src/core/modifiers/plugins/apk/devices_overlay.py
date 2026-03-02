"""DevicesOverlay modification plugin.

Fixes AOD and under-display fingerprint issues for older Android versions.
"""
import re
from pathlib import Path

from src.core.modifiers.plugins.apk.base import ApkModifierPlugin, ApkModifierRegistry


@ApkModifierRegistry.register
class DevicesOverlayModifier(ApkModifierPlugin):
    """Fix AOD and fingerprint for DevicesAndroidOverlay.apk."""
    
    name = "devices_overlay_modifier"
    description = "Fix AOD and under-display fingerprint for Android < 16"
    apk_name = "DevicesAndroidOverlay"
    priority = 80
    
    def check_prerequisites(self) -> bool:
        """Only apply for Android version < 16."""
        # Get Android version
        try:
            version_str = getattr(self.ctx, "base_android_version", "0")
            if "." in str(version_str):
                version_str = str(version_str).split(".")[0]
            base_version = int(version_str)
        except (ValueError, TypeError):
            base_version = 0
        
        if base_version >= 16:
            self.logger.info(f"Stock Android version is {base_version} (>= 16). Skipping AOD fix.")
            return False
        
        return super().check_prerequisites()
    
    def _apply_patches(self, work_dir: Path):
        """Apply AOD and fingerprint fixes."""
        self.logger.info("Processing DevicesAndroidOverlay.apk...")
        self.logger.info("Fixing AOD and under-display fingerprint issues...")
        
        # Pattern to match and replace
        pattern = re.compile(r'(<string\s+name="config_dozeComponent">)[^<]*')
        replacement = r'\1com.android.systemui/com.android.keyguard.doze.MiuiDozeService'
        
        modified_count = 0
        
        for xml_file in work_dir.rglob("*.xml"):
            try:
                content = xml_file.read_text(encoding='utf-8', errors='ignore')
                
                if pattern.search(content):
                    new_content = pattern.sub(replacement, content)
                    
                    if new_content != content:
                        xml_file.write_text(new_content, encoding='utf-8')
                        self.logger.debug(f"Patched {xml_file.name}")
                        modified_count += 1
            except Exception as e:
                self.logger.warning(f"Failed to patch {xml_file}: {e}")
        
        self.logger.info(f"Modified {modified_count} XML file(s)")
