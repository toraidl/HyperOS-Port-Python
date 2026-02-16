import importlib
import logging
import concurrent.futures
from pathlib import Path

from src.utils.xml_utils import XmlUtils


class AppPatcher:
    def __init__(self, context, modifier_instance):
        self.ctx = context
        self.mod = modifier_instance # Reuse utility methods from FrameworkModifier
        self.logger = logging.getLogger("AppPatcher")
        self.xml = XmlUtils()
        # Register modules: APK name keyword -> Module class path
        self.registry = {
            "Settings.apk": "src.modules.settings.SettingsModule",
            "Joyose.apk": "src.modules.joyose.JoyoseModule",
            "*SecurityCenter.apk": "src.modules.securitycenter.SecurityCenterModule",
            "MIUIPackageInstaller.apk": "src.modules.installer.InstallerModule",
            "PowerKeeper.apk": "src.modules.powerkeeper.PowerKeeperModule",
            "MiuiGuardProvider.apk": "src.modules.guard.GuardModule",
        }

    def run(self):
        self.logger.info("Starting App Patching...")
        
        # Collect all patching tasks
        tasks = []
        
        # Iterate through the registry
        for apk_pattern, module_path in self.registry.items():
            # Support wildcard search (e.g., *SecurityCenter.apk) using rglob in target_dir
            target_file = None
            if "*" in apk_pattern:
                # Handle wildcard replacement
                clean_pattern = apk_pattern.replace("*", "")
                for f in self.ctx.target_dir.rglob(f"*{clean_pattern}"):
                    target_file = f
                    break
            else:
                for f in self.ctx.target_dir.rglob(apk_pattern):
                    target_file = f
                    break
            
            if target_file and target_file.exists():
                tasks.append((target_file, module_path))
            else:
                self.logger.warning(f"Skipping {apk_pattern}: File not found.")
        
        # Execute tasks in parallel
        max_workers = 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._process_apk, apk_file, module_path)
                for apk_file, module_path in tasks
            ]
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"App patch failed: {e}")

    def _process_apk(self, apk_file, module_class_path):
        self.logger.info(f"Processing {apk_file.name}...")
        
        # 1. Dynamically load module class
        try:
            module_path, class_name = module_class_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            ModuleClass = getattr(module, class_name)
        except Exception as e:
            self.logger.error(f"Failed to load module {module_class_path}: {e}")
            return

        # 2. Prepare environment
        wd = self.ctx.target_dir.parent / f"temp_{apk_file.stem}"
        if wd.exists(): import shutil; shutil.rmtree(wd)
        
        # 3. Decompile APK (using modifier utility)
        self.mod._apkeditor_decode(apk_file, wd)
        
        patcher = ModuleClass(
            run_smalikit_func=self.mod._run_smalikit,
            context=self.ctx
        )
        
        try:
            patcher.run(wd) # Execute modification
            # 5. Recompile APK
            self.mod._apkeditor_build(wd, apk_file)
            self.logger.info(f"Successfully patched {apk_file.name}")
        except Exception as e:
            self.logger.error(f"Error patching {apk_file.name}: {e}")
        #finally:
        #    if wd.exists(): import shutil; shutil.rmtree(wd)
