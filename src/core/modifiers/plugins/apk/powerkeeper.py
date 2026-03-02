"""PowerKeeper modification plugin.

Unlocks FTP and screen effects.
"""
from pathlib import Path

from src.core.modifiers.plugins.apk.base import ApkModifierPlugin, ApkModifierRegistry


@ApkModifierRegistry.register
class PowerKeeperModifier(ApkModifierPlugin):
    """Modify PowerKeeper.apk to unlock display features."""
    
    name = "powerkeeper_modifier"
    description = "Unlock FTP and screen effects"
    apk_name = "PowerKeeper"
    priority = 76
    
    def _apply_patches(self, work_dir: Path):
        """Apply PowerKeeper patches."""
        self.logger.info("Processing PowerKeeper.apk...")
        
        # Unlock FTP/Screen Effect
        self._unlock_ftp(work_dir)
    
    def _unlock_ftp(self, work_dir: Path):
        """Unlock FTP and screen effects."""
        self.logger.info("Unlocking FTP/Screen Effect...")
        
        # 1. DisplayFrameSetting -> setScreenEffect -> void
        self.smali_patch(
            work_dir,
            iname="DisplayFrameSetting.smali",
            method="setScreenEffect(II)V",
            remake=".locals 0\n    return-void"
        )
        
        # 2. ThermalManager -> getDisplayCtrlCode -> false
        self.smali_patch(
            work_dir,
            iname="ThermalManager.smali",
            method="getDisplayCtrlCode",
            remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
        )
