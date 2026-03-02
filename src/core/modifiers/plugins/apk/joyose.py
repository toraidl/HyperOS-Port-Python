"""Joyose modification plugin.

Disables cloud sync and enables GPU tuner.
"""
from pathlib import Path

from src.core.modifiers.plugins.apk.base import ApkModifierPlugin, ApkModifierRegistry


@ApkModifierRegistry.register
class JoyoseModifier(ApkModifierPlugin):
    """Modify Joyose.apk for performance tweaks."""
    
    name = "joyose_modifier"
    description = "Disable cloud sync and enable GPU tuner"
    apk_name = "Joyose"
    priority = 75
    
    def _apply_patches(self, work_dir: Path):
        """Apply Joyose patches."""
        self.logger.info("Processing Joyose.apk...")
        
        # 1. Disable cloud sync
        self.smali_patch(
            work_dir,
            seek_keyword="job exist, sync local...",
            remake=".locals 0\n    return-void"
        )
        
        # 2. Enable GPU Tuner
        self.smali_patch(
            work_dir,
            seek_keyword="GPUTUNER_SWITCH",
            return_type="Z",
            remake=".locals 1\n    const/4 v0, 0x1\n    return v0"
        )
