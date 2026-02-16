from pathlib import Path
from .base import BaseModule

class PowerKeeperModule(BaseModule):
    def run(self, work_dir: Path):
        self.logger.info("Processing PowerKeeper.apk...")
        
        self._unlock_ftp(work_dir)

    def _unlock_ftp(self, work_dir: Path):
        """Corresponds to powerkeeper_ftp_unlock"""
        self.logger.info("Unlocking FTP/Screen Effect...")
        
        # 1. DisplayFrameSetting -> setScreenEffect -> void
        self.smali_patch(work_dir,
            iname="DisplayFrameSetting.smali",
            method="setScreenEffect(II)V",
            remake=".locals 0\n    return-void"
        )

        # 2. ThermalManager -> getDisplayCtrlCode -> false (0)
        self.smali_patch(work_dir,
            iname="ThermalManager.smali",
            method="getDisplayCtrlCode",
            remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
        )