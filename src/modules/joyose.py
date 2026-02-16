from pathlib import Path
from .base import BaseModule

class JoyoseModule(BaseModule):
    def run(self, work_dir: Path):
        # Disable cloud sync (joyose_disable_cloud)
        self.smali_patch(work_dir, 
            seek_keyword="job exist, sync local...", 
            remake=".locals 0\n    return-void"
        )
        
        # Enable GPU Tuner (joyose_enable_gpu_tuner)
        self.smali_patch(work_dir, 
            seek_keyword="GPUTUNER_SWITCH", 
            return_type="Z", 
            remake=".locals 1\n    const/4 v0, 0x1\n    return v0"
        )
