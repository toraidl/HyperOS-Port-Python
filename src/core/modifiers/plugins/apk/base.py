"""APK-specific modifier plugins.

Extends the plugin system for APK-level modifications.
Uses PortingContext's built-in tools and shell runner.
"""

from abc import abstractmethod
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
import logging
import shutil
import sys
import re

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
    cache_version: str = "1.0"  # Cache version for APK modification results

    def __init__(self, context, logger=None):
        super().__init__(context, logger)
        self.xml = XmlUtils()
        self._work_dir: Optional[Path] = None
        self._apk_path: Optional[Path] = None
        self.quiet = True  # Filter out noisy APKEditor logs
        self._last_line_was_progress = False

    def _log_filter(self, line: str):
        """Filter noisy logs and provide progress feedback."""
        if not line:
            return

        # Strip ANSI escape codes to match keywords accurately
        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        clean_line = ansi_escape.sub("", line)

        # Noisy APKEditor tags to filter out from permanent logs
        noise_keywords = [
            "Encoding:",
            "Decoding:",
            "Building:",
            "Analyzing:",
            "Copying:",
            "Scanning:",
            "Writing:",
            "Compressing:",
            "Adding:",
            "[DECOMPILE]",
            "[BUILD]",
            "[ENCODE]",
            "[DECODING]",
            "[ENCODING]",
        ]

        # Aggressive filtering: if line contains progress-like patterns
        is_noise = any(kw in clean_line for kw in noise_keywords)

        # Also filter lines that are obviously just file processing paths
        if not is_noise:
            if re.search(r"\[(DECOMPILE|BUILD|ENCODE)\]\s+res/", clean_line):
                is_noise = True
            elif re.search(r"\[(DECOMPILE|BUILD|ENCODE)\]\s+smali/", clean_line):
                is_noise = True

        if is_noise and self.quiet:
            # For noise lines, we show a rolling progress on console IF we are not buffered
            if not self.logger.propagate:
                pass
            else:
                # In serial mode, we can do the \r trick for better UI
                sys.stdout.write(f"\r  [BUILD] {clean_line[:80].ljust(85)}")
                sys.stdout.flush()
                self._last_line_was_progress = True
        else:
            # Non-noise lines (errors, warnings, important steps)
            if self._last_line_was_progress:
                # UI output: newline after progress indicator (not a log message)
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._last_line_was_progress = False

            # Log to the actual logger (this will go to both console and file)
            self.logger.info(f"  [SHELL] {line}")

    def check_prerequisites(self) -> bool:
        """Check if target APK exists using cached lookup."""
        self._apk_path = self._find_apk()
        if not self._apk_path:
            self.logger.debug(f"APK {self.apk_name} not found, skipping")
            return False
        return True

    def modify(self) -> bool:
        """Execute APK modification workflow with caching support."""
        if not self._apk_path:
            return False

        self.logger.info(f"Modifying {self.apk_name}...")

        # Check APK modification cache
        cached_apk = self._get_cached_apk()
        if cached_apk:
            self.logger.info(f"Using cached modified APK: {self.apk_name}")
            try:
                shutil.copy2(cached_apk, self._apk_path)
                return True
            except Exception as e:
                self.logger.warning(f"Failed to copy cached APK: {e}, will rebuild")

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
                # Save to cache
                self._save_apk_cache(output_apk)
                return True
            else:
                self.logger.error(f"Failed to recompile {self.apk_name}")
                return False

        except Exception as e:
            self.logger.error(f"Error modifying {self.apk_name}: {e}")
            return False

    def _get_cache_key(self) -> Optional[str]:
        """Generate cache key for this APK modification.

        Returns:
            Cache key string or None if caching not possible
        """
        if not self._apk_path or not self._apk_path.exists():
            return None

        # Get APK hash
        import hashlib

        hash_md5 = hashlib.md5()
        with open(self._apk_path, "rb") as f:
            hash_md5.update(f.read(10 * 1024 * 1024))  # Read first 10MB
        apk_hash = hash_md5.hexdigest()[:16]

        # Combine: APK name, APK hash, modifier class name, cache version
        return f"{self.apk_name}_{apk_hash}_{self.__class__.__name__}_v{self.cache_version}"

    def _get_cached_apk(self) -> Optional[Path]:
        """Check if a cached modified APK exists.

        Returns:
            Path to cached APK or None
        """
        if not hasattr(self.ctx, "cache_manager") or not self.ctx.cache_manager:
            return None

        cache_key = self._get_cache_key()
        if not cache_key:
            return None

        cache_dir = (
            self.ctx.cache_manager._get_apk_cache_dir(
                self.ctx.cache_manager._compute_rom_hash(self.ctx.port.path)
            )
            / cache_key
        )

        cached_apk = cache_dir / "modified.apk"
        if cached_apk.exists():
            return cached_apk
        return None

    def _save_apk_cache(self, apk_path: Path) -> bool:
        """Save modified APK to cache.

        Args:
            apk_path: Path to the modified APK

        Returns:
            True if saved successfully
        """
        if not hasattr(self.ctx, "cache_manager") or not self.ctx.cache_manager:
            return False

        cache_key = self._get_cache_key()
        if not cache_key:
            return False

        try:
            cache_dir = (
                self.ctx.cache_manager._get_apk_cache_dir(
                    self.ctx.cache_manager._compute_rom_hash(self.ctx.port.path)
                )
                / cache_key
            )
            cache_dir.mkdir(parents=True, exist_ok=True)

            cached_apk = cache_dir / "modified.apk"
            shutil.copy2(apk_path, cached_apk)

            # Save metadata
            import json
            from datetime import datetime

            metadata = {
                "cache_key": cache_key,
                "apk_name": self.apk_name,
                "package_name": self.package_name,
                "modifier_class": self.__class__.__name__,
                "cache_version": self.cache_version,
                "cached_at": datetime.now().isoformat(),
            }
            (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

            self.logger.debug(f"Cached modified APK: {self.apk_name}")
            return True

        except Exception as e:
            self.logger.warning(f"Failed to cache APK {self.apk_name}: {e}")
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
        if self.package_name and hasattr(self.ctx, "find_apk_by_package"):
            apk_path = self.ctx.find_apk_by_package(self.package_name)
            if apk_path:
                self.logger.debug(f"Found {self.apk_name} by package name: {self.package_name}")
                return apk_path

        # 2. Try filename lookup (cached)
        if hasattr(self.ctx, "find_apk_by_name"):
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
                on_line=self._log_filter,
            )

            if self._last_line_was_progress:
                # UI output: finalize progress line with newline (not a log message)
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._last_line_was_progress = False

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
                on_line=self._log_filter,
            )

            if self._last_line_was_progress:
                # UI output: finalize progress line with newline (not a log message)
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._last_line_was_progress = False

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
        patcher = SmaliKit(args, logger=self.logger)
        patcher.walk_and_patch(target_path)

    def smali_seek_and_replace(
        self, work_dir: Path, keyword: str, return_value: str, return_type: str = "Z"
    ):
        """Seek keyword and replace return value."""
        remake_code = f".locals 1\n    {return_value}\n    return v0"
        self.smali_patch(
            work_dir=work_dir, seek_keyword=keyword, return_type=return_type, remake=remake_code
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
                if content in f.read_text(encoding="utf-8", errors="ignore"):
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
