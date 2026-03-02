"""APK-specific modifier plugins.

Extends the plugin system for APK-level modifications.
"""
from abc import abstractmethod
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging

from src.core.modifiers.plugin_system import ModifierPlugin
from src.utils.smalikit import SmaliKit
from src.utils.xml_utils import XmlUtils


class ApkModifierPlugin(ModifierPlugin):
    """Base class for APK modification plugins.
    
    Unlike system-level ModifierPlugin, this focuses on modifying
    specific APK files (decompile, patch, recompile).
    """
    
    # APK metadata
    apk_name: str = ""  # Name of the APK to modify (e.g., "MIUIPackageInstaller")
    apk_paths: List[str] = []  # Possible paths to find the APK
    
    def __init__(self, context, logger=None):
        super().__init__(context, logger)
        self.xml = XmlUtils()
        self._work_dir: Optional[Path] = None
        self._apk_path: Optional[Path] = None
    
    def check_prerequisites(self) -> bool:
        """Check if target APK exists."""
        self._apk_path = self._find_apk()
        if not self._apk_path:
            self.logger.debug(f"APK {self.apk_name} not found, skipping")
            return False
        return True
    
    def modify(self) -> bool:
        """Execute APK modification workflow."""
        if not self._apk_path:
            return False
        
        self.logger.info(f"Modifying {self.apk_name}...")
        
        try:
            # 1. Decompile APK
            work_dir = self._decompile_apk(self._apk_path)
            if not work_dir:
                return False
            
            self._work_dir = work_dir
            
            # 2. Apply patches
            self._apply_patches(work_dir)
            
            # 3. Recompile APK
            output_apk = self._recompile_apk(work_dir, self._apk_path)
            
            if output_apk:
                self.logger.info(f"Successfully modified {self.apk_name}")
                return True
            else:
                self.logger.error(f"Failed to recompile {self.apk_name}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error modifying {self.apk_name}: {e}")
            return False
    
    @abstractmethod
    def _apply_patches(self, work_dir: Path):
        """Apply patches to decompiled APK.
        
        Subclasses implement this method to define specific modifications.
        """
        pass
    
    def _find_apk(self) -> Optional[Path]:
        """Find the target APK in the ROM."""
        target_dir = self.ctx.target_dir
        
        # Search in standard APK directories
        search_paths = [
            f"system/app/{self.apk_name}/{self.apk_name}.apk",
            f"system/priv-app/{self.apk_name}/{self.apk_name}.apk",
            f"product/app/{self.apk_name}/{self.apk_name}.apk",
            f"product/priv-app/{self.apk_name}/{self.apk_name}.apk",
            f"system_ext/app/{self.apk_name}/{self.apk_name}.apk",
            f"system_ext/priv-app/{self.apk_name}/{self.apk_name}.apk",
        ]
        
        # Add custom paths if specified
        if self.apk_paths:
            search_paths = self.apk_paths + search_paths
        
        for path_str in search_paths:
            full_path = target_dir / path_str
            if full_path.exists():
                return full_path
        
        # Fallback: search by name
        for pattern in [f"**/{self.apk_name}.apk"]:
            matches = list(target_dir.glob(pattern))
            if matches:
                return matches[0]
        
        return None
    
    def _decompile_apk(self, apk_path: Path) -> Optional[Path]:
        """Decompile APK using apktool."""
        from src.utils.shell import ShellRunner
        
        shell = ShellRunner()
        bin_dir = Path("bin").resolve()
        apktool = bin_dir / "apktool" / "apktool"
        
        # Create work directory
        work_dir = Path("temp") / f"apk_{self.apk_name.lower()}"
        work_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Decompile
            cmd = [str(apktool), "d", "-f", str(apk_path), "-o", str(work_dir)]
            shell.run(cmd)
            
            return work_dir
        except Exception as e:
            self.logger.error(f"Failed to decompile {apk_path}: {e}")
            return None
    
    def _recompile_apk(self, work_dir: Path, original_apk: Path) -> Optional[Path]:
        """Recompile APK using apktool."""
        from src.utils.shell import ShellRunner
        import shutil
        
        shell = ShellRunner()
        bin_dir = Path("bin").resolve()
        apktool = bin_dir / "apktool" / "apktool"
        
        temp_apk = work_dir.parent / f"{self.apk_name}_recompiled.apk"
        
        try:
            # Recompile
            cmd = [str(apktool), "b", "-f", str(work_dir), "-o", str(temp_apk)]
            shell.run(cmd)
            
            # Replace original
            shutil.copy2(temp_apk, original_apk)
            
            # Cleanup
            temp_apk.unlink()
            
            return original_apk
        except Exception as e:
            self.logger.error(f"Failed to recompile {self.apk_name}: {e}")
            return None
    
    # Helper methods for Smali patching
    def smali_patch(self, work_dir: Path, **kwargs):
        """Apply Smali patch using SmaliKit."""
        args = type('Args', (), kwargs)()
        patcher = SmaliKit(args)
        patcher.walk_and_patch(str(work_dir))
    
    def smali_seek_and_replace(self, work_dir: Path, keyword: str, return_value: str, return_type: str = "Z"):
        """Seek keyword and replace return value."""
        self.smali_patch(
            work_dir=work_dir,
            seek_keyword=keyword,
            return_type=return_type,
            remake=f".locals 1\n    {return_value}\n    return v0"
        )
    
    def xml_modify(self, xml_path: Path, xpath: str, value: Any):
        """Modify XML file."""
        # Implementation depends on XmlUtils capabilities
        self.logger.debug(f"XML modify: {xml_path} @ {xpath} = {value}")


class ApkModifierRegistry:
    """Registry for APK modifier plugins."""
    
    _registry: Dict[str, type] = {}
    
    @classmethod
    def register(cls, plugin_class: type) -> type:
        """Decorator to register an APK modifier plugin."""
        name = plugin_class.apk_name or plugin_class.__name__
        cls._registry[name] = plugin_class
        return plugin_class
    
    @classmethod
    def get(cls, name: str) -> Optional[type]:
        """Get registered plugin class by APK name."""
        return cls._registry.get(name)
    
    @classmethod
    def list_all(cls) -> Dict[str, type]:
        """Get all registered APK modifier plugins."""
        return cls._registry.copy()
    
    @classmethod
    def auto_discover(cls, manager):
        """Auto-discover and register all APK modifiers."""
        # Import all APK modifiers to ensure they register
        from src.core.modifiers.plugins.apk import installer
        from src.core.modifiers.plugins.apk import securitycenter
        from src.core.modifiers.plugins.apk import settings
        from src.core.modifiers.plugins.apk import joyose
        from src.core.modifiers.plugins.apk import powerkeeper
        from src.core.modifiers.plugins.apk import devices_overlay
        
        # Plugins auto-register via @ApkModifierRegistry.register decorator
        # Now register them with the plugin manager
        for name, plugin_class in cls._registry.items():
            manager.register(plugin_class)
        
        cls.logger().info(f"Auto-discovered {len(cls._registry)} APK modifiers")
    
    @classmethod
    def logger(cls):
        import logging
        return logging.getLogger("ApkModifierRegistry")
