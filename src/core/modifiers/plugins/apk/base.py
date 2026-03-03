"""APK-specific modifier plugins.

Extends the plugin system for APK-level modifications.
Uses PortingContext's built-in tools and shell runner.
"""
from abc import abstractmethod
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging
import shutil

from src.core.modifiers.plugin_system import ModifierPlugin
from src.utils.smalikit import SmaliKit, SmaliArgs
from src.utils.xml_utils import XmlUtils


class ApkModifierPlugin(ModifierPlugin):
    """Base class for APK modification plugins.
    
    Unlike system-level ModifierPlugin, this focuses on modifying
    specific APK files (decompile, patch, recompile).
    
    Uses PortingContext's tools:
    - ctx.tools.apkeditor_jar: Path to APKEditor.jar
    - ctx.shell.run_java_jar(): Execute Java jar commands
    - ctx.find_apk_by_name(): Find APK by filename (cached)
    - ctx.find_apk_by_package(): Find APK by package name (cached)
    """
    
    # APK metadata
    apk_name: str = ""  # Name of the APK to modify (e.g., "MIUIPackageInstaller")
    package_name: str = ""  # Package name (e.g., "com.miui.packageinstaller")
    apk_paths: List[str] = []  # Possible paths to find the APK (fallback)
    
    def __init__(self, context, logger=None):
        super().__init__(context, logger)
        self.xml = XmlUtils()
        self._work_dir: Optional[Path] = None
        self._apk_path: Optional[Path] = None
    
    def check_prerequisites(self) -> bool:
        """Check if target APK exists using cached lookup."""
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
            # 1. Decompile APK using context's tools
            work_dir = self._decompile_apk(self._apk_path)
            if not work_dir:
                return False
            
            self._work_dir = work_dir
            
            # 2. Apply patches
            self._apply_patches(work_dir)
            
            # 3. Recompile APK using context's tools
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
        """Find the target APK using cached lookups.
        
        Search order:
        1. Package name lookup (most accurate, requires aapt2)
        2. Filename lookup (fast, uses cache)
        3. Custom paths (fallback)
        4. Glob search (last resort)
        """
        # 1. Try package name lookup if specified
        if self.package_name and hasattr(self.ctx, 'find_apk_by_package'):
            apk_path = self.ctx.find_apk_by_package(self.package_name)
            if apk_path:
                self.logger.debug(f"Found {self.apk_name} by package name: {self.package_name}")
                return apk_path
        
        # 2. Try filename lookup (cached)
        if hasattr(self.ctx, 'find_apk_by_name'):
            apk_path = self.ctx.find_apk_by_name(self.apk_name)
            if apk_path:
                self.logger.debug(f"Found {self.apk_name} by filename")
                return apk_path
        
        # 3. Try custom paths if specified
        if self.apk_paths:
            for path_str in self.apk_paths:
                full_path = self.ctx.target_dir / path_str
                if full_path.exists():
                    self.logger.debug(f"Found {self.apk_name} at custom path: {path_str}")
                    return full_path
        
        # 4. Fallback: direct path search
        target_dir = self.ctx.target_dir
        search_paths = [
            f"system/app/{self.apk_name}/{self.apk_name}.apk",
            f"system/priv-app/{self.apk_name}/{self.apk_name}.apk",
            f"product/app/{self.apk_name}/{self.apk_name}.apk",
            f"product/priv-app/{self.apk_name}/{self.apk_name}.apk",
            f"system_ext/app/{self.apk_name}/{self.apk_name}.apk",
            f"system_ext/priv-app/{self.apk_name}/{self.apk_name}.apk",
            f"product/overlay/{self.apk_name}.apk",
        ]
        
        for path_str in search_paths:
            full_path = target_dir / path_str
            if full_path.exists():
                return full_path
        
        # 5. Last resort: glob search
        for pattern in [f"**/{self.apk_name}.apk"]:
            matches = list(target_dir.glob(pattern))
            if matches:
                return matches[0]
        
        return None
    
    def _decompile_apk(self, apk_path: Path) -> Optional[Path]:
        """Decompile APK using APKEditor via context's shell runner."""
        # Use PortingContext's tools
        apkeditor_jar = self.ctx.tools.apkeditor_jar
        
        if not apkeditor_jar.exists():
            self.logger.error(f"APKEditor not found: {apkeditor_jar}")
            return None
        
        # Create work directory in temp folder
        work_dir = Path("temp") / f"apk_{self.apk_name.lower()}"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Use context's shell runner: java -jar APKEditor.jar d -i input.apk -o output
            self.ctx.shell.run_java_jar(
                apkeditor_jar,
                ["d", "-f", "-i", str(apk_path), "-o", str(work_dir)],
                logger=self.logger
            )
            
            self.logger.debug(f"Decompiled {apk_path.name} to {work_dir}")
            return work_dir
        except Exception as e:
            self.logger.error(f"Failed to decompile {apk_path}: {e}")
            return None
    
    def _recompile_apk(self, work_dir: Path, original_apk: Path) -> Optional[Path]:
        """Recompile APK using APKEditor via context's shell runner."""
        # Use PortingContext's tools
        apkeditor_jar = self.ctx.tools.apkeditor_jar
        
        if not apkeditor_jar.exists():
            self.logger.error(f"APKEditor not found: {apkeditor_jar}")
            return None
        
        temp_apk = work_dir.parent / f"{self.apk_name}_recompiled.apk"
        
        try:
            # Use context's shell runner: java -jar APKEditor.jar b -i input_dir -o output.apk
            self.ctx.shell.run_java_jar(
                apkeditor_jar,
                ["b", "-f", "-i", str(work_dir), "-o", str(temp_apk)],
                logger=self.logger
            )
            
            # Replace original
            shutil.copy2(temp_apk, original_apk)
            self.logger.debug(f"Recompiled APK saved to {original_apk}")
            
            # Cleanup
            temp_apk.unlink()
            
            return original_apk
        except Exception as e:
            self.logger.error(f"Failed to recompile {self.apk_name}: {e}")
            return None
    
    # Helper methods for Smali patching
    def smali_patch(self, work_dir: Path, **kwargs):
        """Apply Smali patch using SmaliKit."""
        args = SmaliArgs(**kwargs)
        # Use file_path if provided, otherwise use the whole work_dir
        target_path = args.file_path if args.file_path else str(work_dir)
        patcher = SmaliKit(args)
        patcher.walk_and_patch(target_path)
    
    def smali_seek_and_replace(self, work_dir: Path, keyword: str, return_value: str, return_type: str = "Z"):
        """Seek keyword and replace return value."""
        remake_code = f".locals 1\n    {return_value}\n    return v0"
        self.smali_patch(
            work_dir=work_dir,
            seek_keyword=keyword,
            return_type=return_type,
            remake=remake_code
        )
    
    def xml_modify(self, xml_path: Path, xpath: str, value: Any):
        """Modify XML file."""
        # Implementation depends on XmlUtils capabilities
        self.logger.debug(f"XML modify: {xml_path} @ {xpath} = {value}")
    
    def _find_file(self, work_dir: Path, filename: str) -> Optional[Path]:
        """Find a file in work directory."""
        for f in work_dir.rglob(filename):
            return f
        return None
    
    def _find_file_with_content(self, work_dir: Path, content: str) -> Optional[Path]:
        """Find a file containing specific content."""
        for f in work_dir.rglob("*.smali"):
            try:
                if content in f.read_text(encoding='utf-8', errors='ignore'):
                    return f
            except:
                pass
        return None


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
