"""Base class for framework modifier with utility methods."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.core.modifiers.base_modifier import BaseModifier
from src.core.modifiers.smali_args import SmaliArgs
from src.utils.shell import ShellRunner
from src.utils.smalikit import SmaliKit

if TYPE_CHECKING:
    from src.core.context import PortingContext


class FrameworkModifierBase(BaseModifier):
    """Base class for framework-level modifications with utility methods."""

    # Cache version for JAR modifications
    jar_cache_version: str = "1.0"

    def __init__(self, context: PortingContext) -> None:
        super().__init__(context, "FrameworkModifier")
        self.shell = ShellRunner()
        self.bin_dir = Path("bin").resolve()

        self.apktool_path = self.bin_dir / "apktool" / "apktool"
        self.apkeditor_path = self.bin_dir / "APKEditor.jar"
        self.baksmali_path = self.bin_dir / "baksmali.jar"

        self.temp_dir = self.ctx.target_dir.parent / "temp_modifier"

    def _get_jar_cache_key(self, jar_name: str) -> Optional[str]:
        """Generate cache key for JAR modification.

        Uses Port ROM hash instead of JAR hash to avoid cache misses
        when the JAR has already been modified.

        Args:
            jar_name: Name of the JAR file (e.g., "services.jar")

        Returns:
            Cache key string or None if caching not available
        """
        if not hasattr(self.ctx, "cache_manager") or not self.ctx.cache_manager:
            return None

        try:
            rom_hash = self.ctx.cache_manager._compute_rom_hash(self.ctx.port.path)
        except (FileNotFoundError, AttributeError):
            return None

        # Combine: ROM hash, JAR name, modifier class, cache version
        return f"{rom_hash}_{jar_name}_FrameworkModifier_v{self.jar_cache_version}"

    def _get_cached_jar(self, jar_name: str) -> Optional[Path]:
        """Check if a cached modified JAR exists.

        Args:
            jar_name: Name of the JAR file

        Returns:
            Path to cached JAR or None
        """
        if not hasattr(self.ctx, "cache_manager") or not self.ctx.cache_manager:
            return None

        cache_key = self._get_jar_cache_key(jar_name)
        if not cache_key:
            return None

        # Use shortened cache key for directory name
        cache_dir = Path(self.ctx.cache_manager._get_apk_cache_dir(cache_key[:32])) / cache_key

        cached_jar = cache_dir / "modified.jar"
        if cached_jar.exists():
            return cached_jar
        return None

    def _save_jar_cache(self, jar_name: str, jar_path: Path) -> bool:
        """Save modified JAR to cache.

        Args:
            jar_name: Name of the JAR file
            jar_path: Path to the modified JAR

        Returns:
            True if saved successfully
        """
        if not hasattr(self.ctx, "cache_manager") or not self.ctx.cache_manager:
            return False

        cache_key = self._get_jar_cache_key(jar_name)
        if not cache_key:
            return False

        try:
            # Use shortened cache key for directory name
            cache_dir = Path(self.ctx.cache_manager._get_apk_cache_dir(cache_key[:32])) / cache_key
            cache_dir.mkdir(parents=True, exist_ok=True)

            cached_jar = cache_dir / "modified.jar"
            shutil.copy2(jar_path, cached_jar)

            # Save metadata
            metadata = {
                "cache_key": cache_key,
                "jar_name": jar_name,
                "modifier_class": "FrameworkModifier",
                "cache_version": self.jar_cache_version,
                "cached_at": datetime.now().isoformat(),
            }
            (cache_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

            self.logger.debug(f"Cached modified JAR: {jar_name}")
            return True

        except Exception as e:
            self.logger.warning(f"Failed to cache JAR {jar_name}: {e}")
            return False

    def _run_smalikit(self, **kwargs) -> None:
        """Run SmaliKit with given arguments."""
        args = SmaliArgs(**kwargs)
        patcher = SmaliKit(args, logger=self.logger)
        target = args.file_path if args.file_path else args.path
        if target:
            patcher.walk_and_patch(target)

    def _apkeditor_decode(self, jar_path: Path, out_dir: Path) -> None:
        """Decode JAR/APK using APKEditor."""
        self.shell.run_java_jar(
            self.apkeditor_path, ["d", "-f", "-i", str(jar_path), "-o", str(out_dir)]
        )

    def _apkeditor_build(self, src_dir: Path, out_jar: Path) -> None:
        """Build JAR/APK using APKEditor."""
        self.shell.run_java_jar(
            self.apkeditor_path, ["b", "-f", "-i", str(src_dir), "-o", str(out_jar)]
        )

    def _find_file(self, root: Path, name_pattern: str) -> Path | None:
        """Find file by name pattern recursively."""
        for p in Path(root).rglob(name_pattern):
            if p.is_file():
                return p
        return None

    def _find_file_recursive(self, root: Path, name_pattern: str) -> Path | None:
        """Alias for _find_file for backward compatibility."""
        return self._find_file(root, name_pattern)

    def _replace_text_in_file(self, file_path: Path | None, old: str, new: str) -> None:
        """Replace text in file if it exists."""
        if not file_path or not file_path.exists():
            return
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        if old in content:
            new_content = content.replace(old, new)
            file_path.write_text(new_content, encoding="utf-8")
            self.logger.info(f"Patched {file_path.name}: {old[:20]}... -> {new[:20]}...")

    def _copy_to_next_classes(self, work_dir: Path, source_dir: Path) -> None:
        """Copy smali classes to next available classes directory."""
        max_num = 1
        for d in work_dir.glob("smali/classes*"):
            name = d.name
            if name == "classes":
                num = 1
            else:
                try:
                    num = int(name.replace("classes", ""))
                except ValueError:
                    num = 1
            if num > max_num:
                max_num = num

        target = work_dir / "smali" / f"classes{max_num + 1}"
        shutil.copytree(source_dir, target, dirs_exist_ok=True)
        self.logger.info(f"Copied classes to {target.name}")

    def _extract_register_from_invoke(
        self, content: str, method_signature: str, invoke_signature: str, arg_index: int = 1
    ) -> str | None:
        """Extract register name from invoke instruction in method."""
        method_pattern = re.compile(
            rf"\.method[^\n]*?{re.escape(method_signature)}(.*?)\.end method", re.DOTALL
        )
        method_match = method_pattern.search(content)

        if not method_match:
            self.logger.warning(f"Target method not found: {method_signature}")
            return None

        method_body = method_match.group(1)

        invoke_pattern = re.compile(rf"invoke-\w+\s+{{(.*?)}}\s*,\s+{re.escape(invoke_signature)}")
        invoke_match = invoke_pattern.search(method_body)

        if not invoke_match:
            self.logger.warning(f"Invoke signature not found in method body: {invoke_signature}")
            return None

        matched_regs_str = invoke_match.group(1)
        reg_list = [r.strip() for r in matched_regs_str.split(",") if r.strip()]

        if arg_index < len(reg_list):
            extracted_reg = reg_list[arg_index]
            self.logger.debug(f"Extracted register {extracted_reg} from {method_signature}")
            return extracted_reg
        else:
            self.logger.warning(f"arg_index {arg_index} out of bounds for registers: {reg_list}")
            return None

    def _extract_register_from_local(
        self, content: str, method_signature: str, local_name: str
    ) -> str | None:
        """Extract register name from .local declaration or move-object instructions."""
        method_pattern = re.compile(
            rf"\.method[^\n]*?{re.escape(method_signature)}(.*?)\.end method", re.DOTALL
        )
        method_match = method_pattern.search(content)
        if not method_match:
            return None

        body = method_match.group(1)

        local_pattern = re.compile(rf"\.local\s+([vp]\d+),\s+{re.escape(local_name)}[;:,]")
        match = local_pattern.search(body)
        if match:
            return match.group(1)

        if local_name == '"descriptor"':
            move_match = re.search(r"move-object(?:\/from16)?\s+([vp]\d+),\s+p1", body)
            if move_match:
                return move_match.group(1)
        elif local_name == '"args"':
            move_match = re.search(r"move-object(?:\/from16)?\s+([vp]\d+),\s+p3", body)
            if move_match:
                return move_match.group(1)

        return None
