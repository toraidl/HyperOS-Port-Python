import json
import os
import re
import shutil
import logging
import concurrent.futures
from pathlib import Path

import tempfile
import urllib
import zipfile
from src.utils.shell import ShellRunner
import urllib.request
from urllib.error import URLError
import subprocess

from src.utils.smalikit import SmaliKit
from src.core.config_merger import ConfigMerger
from src.core.config_loader import load_device_config
from src.core.conditions import ConditionEvaluator, BuildContext

class SmaliArgs:
    def __init__(self, **kwargs):
        self.path = None
        self.file_path = None
        self.method = None
        self.seek_keyword = None
        self.iname = None
        self.remake = None
        self.replace_in_method = None
        self.regex_replace = None
        self.delete_in_method = None
        self.delete_method = False
        self.after_line = None
        self.before_line = None
        self.insert_line = None
        self.recursive = False
        self.return_type = None
        
        self.__dict__.update(kwargs)

class SystemModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("Modifier")
        self.shell = ShellRunner()
        
        self.bin_dir = Path("bin").resolve()
        self.apktool = self.bin_dir / "apktool.jar"
        
        self.temp_dir = self.ctx.target_dir.parent / "temp"

        self.merger = ConfigMerger(self.logger)
        self.evaluator = ConditionEvaluator()

    def _load_merged_config(self, filename):
        """
        Load and merge configuration from common, chipset and target layers.
        """
        # Hierarchy: common -> chipset -> target (Stock ROM Code)
        paths = [
            Path("devices/common"),
            Path(f"devices/{getattr(self.ctx, 'base_chipset_family', 'unknown')}"),
            Path(f"devices/{self.ctx.stock_rom_code}")
        ]
        
        # Filter valid paths
        valid_paths = [p for p in paths if p.exists() and p.is_dir()]
        
        config, report = self.merger.load_and_merge(valid_paths, filename)
        
        # Log summary
        if report.loaded_files:
            self.logger.info(f"Merged {filename} from: {', '.join(report.loaded_files)}")
            
        return config

    def _get_build_context(self):
        """Prepare build context for condition evaluation."""
        from src.core.conditions import BuildContext as BaseBuildContext
        ctx = BaseBuildContext()
        
        # Map PortingContext to BuildContext
        ctx.port_android_version = int(self.ctx.port_android_version)
        ctx.base_android_version = int(self.ctx.base_android_version)
        ctx.base_device_code = self.ctx.stock_rom_code
        
        ctx.port_os_version_incremental = self.ctx.port.get_prop("ro.mi.os.version.incremental") or self.ctx.port.get_prop("ro.build.version.incremental", "")
        
        # HyperOS specific
        ctx.is_port_eu_rom = getattr(self.ctx, "is_port_eu_rom", False)
        
        return ctx

    def _evaluate_rule(self, rule):
        """Evaluate if a rule's conditions are met."""
        build_ctx = self._get_build_context()
        passed, reason = self.evaluator.evaluate_with_reason(rule, build_ctx)
        
        if not passed:
            self.logger.debug(f"Rule '{rule.get('description', 'unnamed')}' skipped: {reason}")
            
        return passed

    def run(self):
        self.logger.info("Starting System Modification...")

        self.temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.android_version = int(self.ctx.port.get_prop("ro.build.version.release", "14"))
        except:
            self.android_version = 14

        # Load device configuration
        self.device_config = load_device_config(self.ctx.stock_rom_code, self.logger)

        # Order matters!
        # 1. Install wild_boost BEFORE config migration (if enabled)
        self._process_wild_boost()
        
        # 2. Process file replacements
        self._process_replacements()
        self._migrate_configs()
        
        # 3. Unlock features AFTER config migration
        self._unlock_device_features()

        self._fix_vndk_apex()
        self._fix_vintf_manifest()

        # 4. Apply EU Localization (if enabled/bundle provided)
        if getattr(self.ctx, "is_port_eu_rom", False) and getattr(self.ctx, "eu_bundle", None):
            self._apply_eu_localization()

        self.logger.info("System Modification Completed.")

    def _get_kernel_version(self) -> str:
        """
        Get kernel version from boot image.
        Returns kernel version string like '5.15', '5.10', etc.
        """
        boot_img = self.ctx.repack_images_dir / "boot.img"
        if not boot_img.exists():
            self.logger.warning("boot.img not found, cannot detect kernel version.")
            return "unknown"

        kmi = self._analyze_kmi(boot_img)
        if kmi:
            # kmi format: android14-5.15, extract version part
            match = re.search(r'(\d+\.\d+)', kmi)
            if match:
                return match.group(1)
        return "unknown"

    def _analyze_kmi(self, boot_img):
        """
        Analyze kernel image to extract KMI (Kernel Module Interface) version.
        """
        with tempfile.TemporaryDirectory(prefix="ksu_kmi_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(boot_img, tmp_path / "boot.img")

            try:
                self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path)
            except Exception:
                return None

            kernel_file = tmp_path / "kernel"
            if not kernel_file.exists():
                return None

            try:
                with open(kernel_file, 'rb') as f:
                    content = f.read()

                strings = []
                current = []
                for b in content:
                    if 32 <= b <= 126:
                        current.append(chr(b))
                    else:
                        if len(current) >= 4:
                            strings.append("".join(current))
                        current = []

                pattern = re.compile(r'(?:^|\s)(\d+\.\d+)\S*(android\d+)')
                for s in strings:
                    if "Linux version" in s or "android" in s:
                        match = pattern.search(s)
                        if match:
                            return f"{match.group(2)}-{match.group(1)}"
            except Exception:
                pass
        return None

    def _is_valid_cpio(self, cpio_path: Path) -> bool:
        """
        Check if file has valid CPIO header.
        CPIO magic: '070701' (new ASCII) or '070702' (new CRC)
        """
        if not cpio_path.exists():
            return False
        
        try:
            with open(cpio_path, 'rb') as f:
                magic = f.read(6)
            # CPIO new ASCII format starts with '070701'
            return magic in [b'070701', b'070702', b'\x71\xc7', b'\xc7\x71']
        except Exception:
            return False

    def _process_wild_boost(self):
        """
        Process wild_boost installation based on device configuration.
        Reads config from devices/<codename>/config.json
        """
        wild_boost_cfg = self.device_config.get("wild_boost", {})
        
        if not wild_boost_cfg.get("enable", False):
            self.logger.info("Wild Boost is disabled in configuration.")
            return
        
        self.logger.info("Wild Boost is enabled...")
        
        # 1. Install kernel modules (automatically finds zip in devices/common)
        self._install_wild_boost_kernel_modules()
        
        # 2. Apply HexPatch to libmigui.so for device spoofing
        hexpatch_success = self._apply_libmigui_hexpatch()
        
        # 3. Fallback: Add persist.sys.feas.enable=true if HexPatch not applied
        if not hexpatch_success:
            self.logger.info("HexPatch not applied (libmigui.so not found or already patched).")
            self.logger.info("Adding persist.sys.feas.enable=true as fallback...")
            self._add_feas_property()

    def _add_feas_property(self):
        """
        Add persist.sys.feas.enable=true to mi_ext/etc/build.prop.
        This property enables wild_boost on newer systems without HexPatch.
        """
        target_dir = self.ctx.target_dir
        prop_file = target_dir / "mi_ext" / "etc" / "build.prop"
        
        # Create mi_ext directory if not exists
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Read existing content or create new
        if prop_file.exists():
            content = prop_file.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
        else:
            lines = []
            content = ""
        
        # Check if property already exists
        if "persist.sys.feas.enable=true" in content:
            self.logger.info("persist.sys.feas.enable=true already exists.")
            return
        
        # Add property
        lines.append("persist.sys.feas.enable=true")
        prop_file.write_text("\n".join(lines) + "\n", encoding='utf-8')
        self.logger.info("Added persist.sys.feas.enable=true to mi_ext/build.prop")
    
    def _apply_libmigui_hexpatch(self):
        """
        Apply HexPatch to libmigui.so for device spoofing.
        Returns True if patch was applied, False if not needed/failed.
        """
        self.logger.info("Applying HexPatch to libmigui.so...")
        
        # Find libmigui.so in target directory
        target_dir = self.ctx.target_dir
        libmigui_files = list(target_dir.rglob("libmigui.so"))
        
        if not libmigui_files:
            self.logger.debug("libmigui.so not found, HexPatch skipped.")
            return False
        
        # Hex patches for property name spoofing
        patches = [
            {
                "old": "726F2E70726F647563742E70726F647563742E6E616D65",  # ro.product.product.name
                "new": "726F2E70726F647563742E73706F6F6665642E6E616D65"   # ro.product.spoofed.name
            },
            {
                "old": "726F2E70726F647563742E646576696365",  # ro.product.device
                "new": "726F2E73706F6F6665642E646576696365"   # ro.spoofed.device
            }
        ]
        
        patched_count = 0
        for libmigui in libmigui_files:
            try:
                content = libmigui.read_bytes()
                modified = False
                
                for patch in patches:
                    old_bytes = bytes.fromhex(patch["old"])
                    new_bytes = bytes.fromhex(patch["new"])
                    
                    if old_bytes in content:
                        content = content.replace(old_bytes, new_bytes)
                        modified = True
                        self.logger.debug(f"  Patched: {libmigui.relative_to(target_dir)}")
                
                if modified:
                    libmigui.write_bytes(content)
                    patched_count += 1
                else:
                    self.logger.debug(f"  Already patched or no match: {libmigui.relative_to(target_dir)}")
                    
            except Exception as e:
                self.logger.error(f"Failed to patch {libmigui}: {e}")
        
        if patched_count > 0:
            self.logger.info(f"HexPatch applied to {patched_count} libmigui.so file(s).")
            return True
        else:
            self.logger.debug("No files were patched.")
            return False

    def _install_wild_boost_kernel_modules(self, custom_source: Path = None):
        """
        Handle kernel module installation.
        If custom_source is provided (from replacements.json), use its directory and prefix.
        Otherwise, default to devices/common/wild_boost_{version}.zip
        """
        kernel_version = self._get_kernel_version()
        self.logger.info(f"Detected kernel version: {kernel_version}")
        
        if kernel_version == "unknown":
            self.logger.error("Cannot detect kernel version, wild_boost modules skipped.")
            return
        
        # Determine the zip path
        if custom_source:
            zip_dir = custom_source.parent
            base_name = custom_source.stem
            matching_zip = zip_dir / f"{base_name}_{kernel_version}.zip"
        else:
            # Default location
            matching_zip = Path(f"devices/common/wild_boost_{kernel_version}.zip")
        
        if not matching_zip.exists():
            self.logger.error(f"Wild boost zip not found: {matching_zip}")
            return
        
        self.logger.info(f"Using wild_boost package: {matching_zip.name}")
        
        with tempfile.TemporaryDirectory(prefix="wild_boost_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            with zipfile.ZipFile(matching_zip, 'r') as z:
                z.extractall(tmp_path)
            
            ko_files = list(tmp_path.rglob("*.ko"))
            if not ko_files:
                self.logger.error("No kernel modules (*.ko) found in zip.")
                return
            
            self.logger.info(f"Found {len(ko_files)} modules to install: {[f.name for f in ko_files]}")
            
            # Auto-detect installation location
            vendor_dlkm_dir = self.ctx.target_dir / "vendor_dlkm"
            
            if kernel_version == "5.10":
                self._install_wild_boost_vendor_boot(ko_files)
            elif vendor_dlkm_dir.exists():
                self._install_wild_boost_vendor_dlkm(ko_files)
            else:
                self.logger.error("No suitable location (vendor_boot/vendor_dlkm) for wild_boost.")
    
    def _install_wild_boost_vendor_boot(self, ko_files):
        """
        Install wild_boost modules to vendor_boot ramdisk.
        """
        self.logger.info(f"Installing {len(ko_files)} modules to vendor_boot ramdisk...")
        
        vendor_boot_img = self.ctx.repack_images_dir / "vendor_boot.img"
        if not vendor_boot_img.exists():
            self.logger.error("vendor_boot.img not found.")
            return
        
        # Create temp directory for unpacking
        work_dir = self.ctx.target_dir.parent / "temp" / "vendor_boot_work"
        if work_dir.exists(): shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vendor_boot_img, work_dir / "vendor_boot.img")
        
        # Unpack
        self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "vendor_boot.img"], cwd=work_dir)
        ramdisk_cpio = work_dir / "ramdisk.cpio"
        
        # Decompress
        self.shell.run([str(self.ctx.tools.magiskboot), "decompress", "ramdisk.cpio", "ramdisk.cpio.decomp"], cwd=work_dir)
        if (work_dir / "ramdisk.cpio.decomp").exists():
            ramdisk_cpio.unlink()
            (work_dir / "ramdisk.cpio.decomp").rename(ramdisk_cpio)

        # Extract to find paths
        self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "extract"], cwd=work_dir)

        # Find modules directory
        modules_load_files = list(work_dir.rglob("modules.load*"))
        if not modules_load_files:
            modules_dir_rel = Path("lib/modules")
        else:
            modules_dir_rel = modules_load_files[0].parent.relative_to(work_dir)
        
        self.logger.info(f"Modules directory in ramdisk: {modules_dir_rel}")

        # 1. Add/Replace modules in CPIO
        for ko_file in ko_files:
            dest_path = modules_dir_rel / ko_file.name
            self.logger.info(f"  Adding/Replacing: {dest_path}")
            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", f"add 0644 {dest_path} {ko_file}"], cwd=work_dir)

        # 2. Update modules.load*
        for load_file in modules_load_files:
            load_rel = load_file.relative_to(work_dir)
            content = load_file.read_text(errors='ignore')
            lines = content.splitlines()
            
            modified = False
            for ko_file in ko_files:
                if ko_file.name not in content:
                    # Append new modules to the end
                    lines.append(ko_file.name)
                    modified = True
            
            if modified:
                self.logger.info(f"  Updating load file: {load_rel}")
                load_file.write_text("\n".join(lines) + "\n")
                self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", f"add 0644 {load_rel} {load_file}"], cwd=work_dir)

        # 3. Update modules.dep
        dep_file = work_dir / modules_dir_rel / "modules.dep"
        if dep_file.exists():
            self.logger.info(f"  Updating modules.dep...")
            content = dep_file.read_text(errors='ignore')
            lines = content.splitlines()
            
            # For perfmgr.ko, we use the specific dependency line
            prefix = f"/{modules_dir_rel}/" if str(modules_dir_rel).startswith("lib") else f"lib/modules/"
            perfmgr_dep = f"{prefix}perfmgr.ko: {prefix}qcom-dcvs.ko {prefix}dcvs_fp.ko {prefix}qcom_rpmh.ko {prefix}cmd-db.ko {prefix}qcom_ipc_logging.ko {prefix}minidump.ko {prefix}smem.ko {prefix}sched-walt.ko {prefix}qcom-cpufreq-hw.ko {prefix}metis.ko {prefix}mi_schedule.ko"
            
            new_lines = []
            perfmgr_found = False
            for line in lines:
                if "perfmgr.ko:" in line:
                    new_lines.append(perfmgr_dep)
                    perfmgr_found = True
                else:
                    new_lines.append(line)
            
            if not perfmgr_found:
                new_lines.append(perfmgr_dep)
            
            dep_file.write_text("\n".join(new_lines) + "\n")
            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", f"add 0644 {dep_file.relative_to(work_dir)} {dep_file}"], cwd=work_dir)

        # 4. Repack
        self.logger.info("Repacking vendor_boot.img...")
        self.shell.run([str(self.ctx.tools.magiskboot), "repack", "vendor_boot.img"], cwd=work_dir)
        
        new_img = work_dir / "new-boot.img"
        if new_img.exists():
            shutil.copy2(new_img, vendor_boot_img)
            self.logger.info("vendor_boot.img updated successfully.")
        else:
            self.logger.error("Failed to repack vendor_boot.img - no output file found.")
            return
        
        shutil.rmtree(work_dir)
        self.logger.info("wild_boost installation completed for kernel 5.10.")
    
    def _install_wild_boost_vendor_dlkm(self, ko_files):
        """
        Install wild_boost modules to vendor_dlkm.
        """
        self.logger.info(f"Installing {len(ko_files)} modules to vendor_dlkm...")
        
        target_dir = self.ctx.target_dir / "vendor_dlkm" / "lib" / "modules"
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Copy modules
        for ko_file in ko_files:
            dest_ko = target_dir / ko_file.name
            self.logger.info(f"  Copying {ko_file.name} to {dest_ko}")
            shutil.copy2(ko_file, dest_ko)
        
        # 2. Update modules.load
        modules_load = target_dir / "modules.load"
        if modules_load.exists():
            content = modules_load.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
            modified = False
            for ko_file in ko_files:
                if ko_file.name not in content:
                    lines.append(ko_file.name)
                    modified = True
            if modified:
                modules_load.write_text("\n".join(lines) + "\n", encoding='utf-8')
        else:
            modules_load.write_text("\n".join([f.name for f in ko_files]) + "\n", encoding='utf-8')
        
        # 3. Update modules.dep
        modules_dep = target_dir / "modules.dep"
        dep_prefix = "/vendor/lib/modules/"
        if modules_dep.exists():
            content = modules_dep.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
            
            perfmgr_dep = f"{dep_prefix}perfmgr.ko: {dep_prefix}qcom-dcvs.ko {dep_prefix}dcvs_fp.ko {dep_prefix}qcom_rpmh.ko {dep_prefix}cmd-db.ko {dep_prefix}qcom_ipc_logging.ko {dep_prefix}minidump.ko {dep_prefix}smem.ko {dep_prefix}sched-walt.ko {dep_prefix}qcom-cpufreq-hw.ko {dep_prefix}metis.ko {dep_prefix}mi_schedule.ko"
            
            new_lines = []
            perfmgr_found = False
            for line in lines:
                if "perfmgr.ko:" in line:
                    new_lines.append(perfmgr_dep)
                    perfmgr_found = True
                else:
                    new_lines.append(line)
            
            if not perfmgr_found:
                new_lines.append(perfmgr_dep)
            
            modules_dep.write_text("\n".join(new_lines) + "\n", encoding='utf-8')
        else:
            # If no dep file, at least create the entry for perfmgr
            modules_dep.write_text(f"{dep_prefix}perfmgr.ko:\n", encoding='utf-8')
        
        self.logger.info("wild_boost installation completed for vendor_dlkm.")

    def _process_replacements(self):
        """
        Execute file/directory replacements defined in replacements.json.
        """
        config = self._load_merged_config("replacements.json")
        replacements = config.get("replacements", [])
        if not replacements:
            return

        self.logger.info(f"Processing {len(replacements)} file replacements...")

        stock_root = self.ctx.stock.extracted_dir
        target_root = self.ctx.target_dir

        for rule in replacements:
            if not self._evaluate_rule(rule):
                continue

            desc = rule.get("description", "Unknown Rule")
            rtype = rule.get("type", "file")
            self.logger.info(f"Applying replacement rule: {desc}")

            try:
                if rtype == "unzip_override":
                    self._handle_unzip_override(rule)
                elif rtype == "wild_boost":
                    # Handle wild_boost installation based on kernel version
                    self._install_wild_boost_kernel_modules(Path(rule["source"]))
                elif rtype == "copy_file_internal":
                    self._handle_copy_file_internal(rule)
                elif rtype == "remove_files":
                    self._handle_remove_files(rule)
                elif rtype == "hexpatch":
                    self._handle_hexpatch(rule)
                elif rtype == "append_text":
                    self._handle_append_text(rule)
                elif rtype == "copy_local":
                    self._handle_copy_local(rule)
                else:
                    # Legacy 'file' type logic
                    self._handle_legacy_replacement(rule)
            except Exception as e:
                self.logger.error(f"Failed to apply rule '{desc}': {e}")

    def _handle_copy_local(self, rule):
        # source is relative to project root, target is relative to target_dir
        source = Path(rule["source"])
        if not source.exists():
            self.logger.warning(f"  Local source not found: {rule['source']}")
            return

        target_val = rule["target"]
        target_files = []
        
        if "/" in target_val:
            # Full path relative to target_dir
            tf = self.ctx.target_dir / target_val
            target_files.append(tf)
        else:
            # Just a filename, search for it in target
            target_files = list(self.ctx.target_dir.rglob(target_val))

        if not target_files:
            if rule.get("ensure_exists", False):
                 # If it doesn't exist but we must have it, we need a path. 
                 # This handler is better with explicit paths or rglob if it's a replacement.
                 self.logger.warning(f"  Target not found for copy_local: {target_val}")
            return

        for target_file in target_files:
            self.logger.debug(f"    [Copy Local] {source} -> {target_file.relative_to(self.ctx.target_dir)}")
            if not target_file.parent.exists():
                target_file.parent.mkdir(parents=True, exist_ok=True)
            
            if source.is_dir():
                shutil.copytree(source, target_file, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target_file)

    def _handle_append_text(self, rule):
        target_file = self.ctx.target_dir / rule["target"]
        if not target_file.exists():
            self.logger.warning(f"  AppendText target not found: {rule['target']}")
            return
        
        text = rule.get("text", "")
        if not text: return

        self.logger.info(f"  Appending text to {rule['target']}...")
        content = target_file.read_text(encoding='utf-8', errors='ignore')
        
        if text not in content:
            with open(target_file, "a", encoding='utf-8') as f:
                f.write(f"\n{text}\n")
            self.logger.debug(f"    Appended: {text.strip()}")

    def _handle_hexpatch(self, rule):
        target_val = rule["target"]
        target_files = []
        
        if "/" in target_val:
            # Full path relative to target_dir
            tf = self.ctx.target_dir / target_val
            if tf.exists():
                target_files.append(tf)
        else:
            # Just a filename, search for it
            target_files = list(self.ctx.target_dir.rglob(target_val))

        if not target_files:
            self.logger.warning(f"  HexPatch target not found: {target_val}")
            return

        for target_file in target_files:
            self.logger.info(f"  HexPatching {target_file.relative_to(self.ctx.target_dir)}...")
            content = target_file.read_bytes()
            
            modified = False
            for patch in rule.get("patches", []):
                old_hex = patch["old"]
                new_hex = patch["new"]
                
                old_bytes = bytes.fromhex(old_hex)
                new_bytes = bytes.fromhex(new_hex)
                
                if old_bytes in content:
                    content = content.replace(old_bytes, new_bytes)
                    modified = True
                    self.logger.debug(f"    Patched hex: {old_hex[:10]}... -> {new_hex[:10]}...")
            
            if modified:
                target_file.write_bytes(content)

    def _handle_unzip_override(self, rule):
        source_zip = Path(rule["source"])
        if not source_zip.exists():
            self.logger.warning(f"  Source zip not found: {source_zip}")
            return
        
        target_dir = self.ctx.target_dir
        if "target" in rule:
            target_dir = target_dir / rule["target"]
            
        self.logger.debug(f"    [Unzip] {source_zip.name} -> {target_dir.relative_to(self.ctx.target_dir)}")
        with zipfile.ZipFile(source_zip, 'r') as z:
            z.extractall(target_dir)

    def _handle_copy_file_internal(self, rule):
        # source/target are relative to target_dir (e.g. "odm/etc/xxx" -> "product/etc/xxx")
        source = self.ctx.target_dir / rule["source"]
        target = self.ctx.target_dir / rule["target"]
        
        if not source.exists():
            if rule.get("ensure_exists", False):
                self.logger.warning(f"  Internal source not found: {rule['source']}")
            return

        if not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            
        self.logger.debug(f"    [Copy Internal] {rule['source']} -> {rule['target']}")
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)

    def _handle_remove_files(self, rule):
        target_root = self.ctx.target_dir
        files = rule.get("files", [])
        search_path = rule.get("search_path", "")
        
        for pattern in files:
            root = target_root / search_path
            for item in root.glob(pattern):
                self.logger.info(f"  Removing: {item.relative_to(target_root)}")
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

    def _handle_legacy_replacement(self, rule):
        stock_root = self.ctx.stock.extracted_dir
        target_root = self.ctx.target_dir
        
        search_path = rule.get("search_path", "")
        match_mode = rule.get("match_mode", "exact")
        ensure_exists = rule.get("ensure_exists", False)
        files = rule.get("files", [])

        # Define search roots
        rule_stock_root = stock_root / search_path
        rule_target_root = target_root / search_path

        if not rule_stock_root.exists():
            return

        for pattern in files:
            sources = []
            if match_mode == "glob":
                sources = list(rule_stock_root.glob(pattern))
            elif match_mode == "recursive":
                sources = list(rule_stock_root.rglob(pattern))
            else:
                exact_file = rule_stock_root / pattern
                if exact_file.exists():
                    sources = [exact_file]
            
            for src_item in sources:
                rel_name = src_item.name
                target_item = rule_target_root / rel_name
                
                found_in_target = False
                if match_mode == "recursive":
                    candidates = list(rule_target_root.rglob(rel_name))
                    if candidates:
                        target_item = candidates[0]
                        found_in_target = True
                else:
                    if target_item.exists():
                        found_in_target = True
                        
                should_copy = found_in_target or ensure_exists
                if should_copy:
                    if not target_item.parent.exists():
                        target_item.parent.mkdir(parents=True, exist_ok=True)
                    
                    if target_item.exists():
                        self.logger.debug(f"    [Replace] {src_item.relative_to(stock_root)} -> {target_item.relative_to(target_root)}")
                        if target_item.is_dir(): shutil.rmtree(target_item)
                        else: target_item.unlink()
                    else:
                        self.logger.debug(f"    [Add New] {src_item.relative_to(stock_root)} -> {target_item.relative_to(target_root)}")
                    
                    if src_item.is_dir():
                        shutil.copytree(src_item, target_item, symlinks=True, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src_item, target_item)
                else:
                    self.logger.debug(f"    [Skip] {src_item.name} (Target missing and ensure_exists=False)")

    def _load_replacement_config(self):
        """Deprecated in favor of _load_merged_config"""
        return []

    def _apply_eu_localization(self):
        bundle_path = Path(self.ctx.eu_bundle)
        if not bundle_path.exists():
            self.logger.warning(f"EU Bundle not found at {bundle_path}, skipping localization.")
            return

        self.logger.info(f"Applying EU Localization Bundle from {bundle_path}...")
        
        with tempfile.TemporaryDirectory(prefix="eu_bundle_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            
            # 1. Extract Bundle
            try:
                with zipfile.ZipFile(bundle_path, 'r') as z:
                    z.extractall(tmp_path)
            except Exception as e:
                self.logger.error(f"Failed to extract EU bundle: {e}")
                return

            # 2. Iterate and Smart Replace
            # Walk through extracted files to find APKs
            for apk_file in tmp_path.rglob("*.apk"):
                # Get package name using aapt2
                pkg_name = self._get_package_name(apk_file)
                if not pkg_name:
                    continue
                
                # relative path inside bundle (e.g. product/app/MiuiCamera/MiuiCamera.apk)
                # We need to determine the root relative to the bundle structure.
                # Assuming bundle structure mirrors system root (e.g. system/..., product/...)
                
                # Find matching app in Target ROM
                # Scan common app directories in target
                found_in_target = False
                target_roots = [
                    self.ctx.target_dir / "system/app",
                    self.ctx.target_dir / "system/priv-app",
                    self.ctx.target_dir / "product/app",
                    self.ctx.target_dir / "product/priv-app",
                    self.ctx.target_dir / "system_ext/app",
                    self.ctx.target_dir / "system_ext/priv-app"
                ]
                
                for root in target_roots:
                    if not root.exists(): continue
                    
                    # Search recursively in this app root
                    for target_apk in root.rglob("*.apk"):
                        target_pkg = self._get_package_name(target_apk)
                        if target_pkg == pkg_name:
                            # FOUND MATCH!
                            app_dir = target_apk.parent
                            self.logger.info(f"Replacing EU App: {pkg_name}")
                            self.logger.info(f"  - Removing: {app_dir}")
                            
                            # Delete old dir
                            shutil.rmtree(app_dir)
                            
                            # Calculate new destination
                            # We place the new app in the SAME location structure as the bundle
                            # relative_path = apk_file.relative_to(tmp_path)
                            # dest_path = self.ctx.target_dir / relative_path
                            
                            # Actually, we should probably place it where the old one was to be safe?
                            # OR trust the bundle structure. 
                            # If we trust bundle structure, we just copy.
                            # But we must delete the old one first to avoid duplicates if path differs.
                            
                            found_in_target = True
                            break
                    if found_in_target: break
                
                if not found_in_target:
                    self.logger.info(f"Adding new EU App: {pkg_name}")

            # 3. Merging Bundle Files
            # Now that we've cleaned up conflicts, simply overlay the bundle
            self.logger.info("Merging EU Bundle files into Target ROM...")
            shutil.copytree(tmp_path, self.ctx.target_dir, dirs_exist_ok=True)

    def _get_package_name(self, apk_path):
        try:
            # aapt2 dump packagename <apk>
            # Output: package: name='com.android.chrome'
            cmd = [str(self.ctx.tools.aapt2), "dump", "packagename", str(apk_path)]
            result = self.shell.run(cmd, capture_output=True, check=False)
            if result.returncode == 0:
                output = result.stdout.strip()
                # Parse "package: name='com.foo.bar'"
                if "package: name=" in output:
                    return output.split("'")[1]
            return None
        except Exception:
            return None

    def _unlock_device_features(self):
        """
        Unlock device features based on JSON configuration (Common + Device specific).
        Note: wild_boost related features are only applied if wild_boost is enabled in config.json.
        """
        self.logger.info("Unlocking device features...")

        # 1. Load Configuration
        config = self._load_feature_config()
        if not config:
            return

        # 2. Check if wild_boost is enabled in config.json
        # If wild_boost kernel module is not installed, skip wild_boost related features
        wild_boost_enabled = self.device_config.get("wild_boost", {}).get("enable", False)
        
        # Filter xml_features - remove wild_boost features if not enabled
        xml_features = config.get("xml_features", {})
        if not wild_boost_enabled:
            # Remove wild_boost related features
            xml_features = {k: v for k, v in xml_features.items() 
                          if not k.startswith("support_wild_boost")}
            self.logger.info("Wild Boost disabled in config.json - skipping wild_boost features.")
        
        if xml_features:
            self._apply_xml_features(xml_features)

        # 3. Filter build_props - remove wild_boost related props if not enabled
        build_props = config.get("build_props", {})
        if not wild_boost_enabled and build_props:
            # Remove spoofing props that are wild_boost specific
            product_props = build_props.get("product", {})
            if product_props:
                # Keep only non-wild_boost props
                filtered_props = {k: v for k, v in product_props.items() 
                                if not k.startswith("ro.product.spoofed") 
                                and not k.startswith("ro.spoofed")
                                and not k.startswith("persist.prophook")}
                if filtered_props:
                    build_props["product"] = filtered_props
                else:
                    build_props.pop("product", None)
        
        if build_props:
            self._apply_build_props(build_props)

        # 4. Apply EU Localization Props (if enabled)
        enable_eu_loc = config.get("enable_eu_localization", False) or getattr(self.ctx, "is_port_eu_rom", False)

        if enable_eu_loc:
            self.logger.info("Enabling EU Localization properties...")
            eu_cfg_path = Path("devices/common/eu_localization.json")
            if eu_cfg_path.exists():
                try:
                    with open(eu_cfg_path, 'r') as f:
                        eu_config = json.load(f)
                    eu_props = eu_config.get("build_props", {})
                    self._apply_build_props(eu_props)
                except Exception as e:
                    self.logger.error(f"Failed to apply EU localization props: {e}")

    def _load_feature_config(self):
        config = {}
        
        # Load Common Config
        common_cfg = Path("devices/common/features.json")
        if common_cfg.exists():
            try:
                with open(common_cfg, 'r') as f:
                    config = json.load(f)
                self.logger.info("Loaded common features config.")
            except Exception as e:
                self.logger.error(f"Failed to load common features: {e}")

        # Load Device Config (Override)
        device_cfg = Path(f"devices/{self.ctx.stock_rom_code}/features.json")
        if device_cfg.exists():
            try:
                with open(device_cfg, 'r') as f:
                    device_config = json.load(f)
                
                # Deep merge logic
                for key, value in device_config.items():
                    if isinstance(value, dict) and key in config:
                        config[key].update(value)
                    else:
                        config[key] = value
                self.logger.info(f"Loaded device features config for {self.ctx.stock_rom_code}.")
            except Exception as e:
                self.logger.error(f"Failed to load device features: {e}")
        
        return config

    def _apply_xml_features(self, features):
        feat_dir = self.ctx.target_dir / "product/etc/device_features"
        if not feat_dir.exists():
            self.logger.warning("device_features directory not found.")
            return

        # Target file: usually matches stock code, or just find any XML
        xml_file = feat_dir / f"{self.ctx.stock_rom_code}.xml"
        if not xml_file.exists():
            # Fallback: try finding any XML in the folder
            try:
                xml_file = next(feat_dir.glob("*.xml"))
            except StopIteration:
                self.logger.warning("No device features XML found.")
                return

        self.logger.info(f"Modifying features in {xml_file.name}...")
        content = xml_file.read_text(encoding='utf-8')
        
        modified = False
        for name, value in features.items():
            str_value = str(value).lower() # true/false
            
            # Check existence
            # Regex to find <bool name="feature_name">...</bool>
            pattern = re.compile(rf'<bool name="{re.escape(name)}">.*?</bool>')
            
            if pattern.search(content):
                # Update existing
                new_tag = f'<bool name="{name}">{str_value}</bool>'
                new_content = pattern.sub(new_tag, content)
                if new_content != content:
                    content = new_content
                    modified = True
                    self.logger.debug(f"Updated feature: {name} = {str_value}")
            else:
                # Insert new (before </features>)
                if "</features>" in content:
                    new_tag = f'    <bool name="{name}">{str_value}</bool>\n</features>'
                    content = content.replace("</features>", new_tag)
                    modified = True
                    self.logger.debug(f"Added feature: {name} = {str_value}")
        
        if modified:
            xml_file.write_text(content, encoding='utf-8')

    def _apply_build_props(self, props_map):
        for partition, props in props_map.items():
            if partition == "vendor":
                prop_file = self.ctx.target_dir / "vendor/build.prop"
            elif partition == "product":
                prop_file = self.ctx.target_dir / "product/etc/build.prop"
            else:
                continue
            
            if not prop_file.exists():
                continue
                
            content = prop_file.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
            new_lines = []
            
            # Simple parsing to avoid duplicates
            existing_keys = set()
            for line in lines:
                if "=" in line and not line.strip().startswith("#"):
                    existing_keys.add(line.split("=")[0].strip())
                new_lines.append(line)
            
            appended = False
            for key, value in props.items():
                if key not in existing_keys:
                    new_lines.append(f"{key}={value}")
                    self.logger.debug(f"Appended prop to {partition}: {key}={value}")
                    appended = True
                # If we wanted to update existing props, we'd need more complex logic here
            
            if appended:
                prop_file.write_text("\n".join(new_lines) + "\n", encoding='utf-8')

    def _find_file_recursive(self, root_dir: Path, filename: str) -> Path | None:
        if not root_dir.exists(): return None
        try:
            return next(root_dir.rglob(filename))
        except StopIteration:
            return None

    def _find_dir_recursive(self, root_dir: Path, dirname: str) -> Path | None:
        if not root_dir.exists(): return None
        for p in root_dir.rglob(dirname):
            if p.is_dir() and p.name == dirname:
                return p
        return None

    def _migrate_configs(self):
        """
        Migrate configurations from Stock to Port.
        Note: General migrations like displayconfig, device_features, and device_info.json
        are now handled via devices/common/replacements.json.
        """
        self.logger.info("Configuration migration (via replacements.json) completed.")

    def _apktool_decode(self, apk_path: Path, out_dir: Path):
        self.shell.run_java_jar(self.apktool, ["d", str(apk_path), "-o", str(out_dir), "-f"])
    
    def _apktool_build(self, src_dir: Path, out_apk: Path):
        self.shell.run_java_jar(self.apktool, ["b", str(src_dir), "-o", str(out_apk),"-f"])

    def _fix_vndk_apex(self):
        vndk_version = self.ctx.stock.get_prop("ro.vndk.version")
        
        if not vndk_version:
             for prop in (self.ctx.stock.extracted_dir / "vendor").rglob("*.prop"):
                 try:
                     with open(prop, errors='ignore') as f:
                         for line in f:
                             if "ro.vndk.version=" in line:
                                 vndk_version = line.split("=")[1].strip()
                                 break
                 except: pass
                 if vndk_version: break
        
        if not vndk_version: return

        apex_name = f"com.android.vndk.v{vndk_version}.apex"
        stock_apex = self._find_file_recursive(self.ctx.stock.extracted_dir / "system_ext/apex", apex_name)
        target_apex_dir = self.ctx.target_dir / "system_ext/apex"
        
        if stock_apex and target_apex_dir.exists():
            target_file = target_apex_dir / apex_name
            if not target_file.exists():
                self.logger.info(f"Copying missing VNDK Apex: {apex_name}")
                shutil.copy2(stock_apex, target_file)
    
    def _apply_device_overrides(self):
        base_code = self.ctx.stock_rom_code
        port_ver = self.ctx.port_android_version
        
        override_src = Path(f"devices/{base_code}/override/{port_ver}").resolve()
        
        if not override_src.exists() or not override_src.is_dir():
            self.logger.warning(f"Device overlay dir not found: {override_src}")
            return

        self.logger.info(f"Applying device overrides from: {override_src}")

        has_nfc_override = False
        for f in override_src.rglob("*.apk"):
            name = f.name.lower()
            if name.startswith("nqnfcnci") or name.startswith("nfc_st"):
                has_nfc_override = True
                break
        
        if has_nfc_override:
            self.logger.info("Detected NFC override, cleaning old NFC directories in target...")
            for p in self.ctx.target_dir.rglob("*"):
                if p.is_dir():
                    name = p.name.lower()
                    if name.startswith("nqnfcnci") or name.startswith("nfc_st"):
                        self.logger.info(f"Removing old NFC dir: {p}")
                        shutil.rmtree(p)

        self.logger.info("Copying override files...")
        try:
            shutil.copytree(override_src, self.ctx.target_dir, dirs_exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to copy overrides: {e}")

    def _fix_vintf_manifest(self):
        self.logger.info("Checking VINTF manifest for VNDK version...")

        vndk_version = self.ctx.stock.get_prop("ro.vndk.version")
        if not vndk_version:
            vendor_prop = self.ctx.target_dir / "vendor/build.prop"
            if vendor_prop.exists():
                try:
                    content = vendor_prop.read_text(encoding='utf-8', errors='ignore')
                    match = re.search(r"ro\.vndk\.version=(.*)", content)
                    if match:
                        vndk_version = match.group(1).strip()
                except: pass

        if not vndk_version:
            self.logger.warning("Could not determine VNDK version, skipping VINTF fix.")
            return

        self.logger.info(f"Target VNDK Version: {vndk_version}")

        target_xml = self._find_file_recursive(self.ctx.target_dir / "system_ext", "manifest.xml")
        if not target_xml:
            self.logger.warning("manifest.xml not found.")
            return

        original_content = target_xml.read_text(encoding='utf-8')
        
        if f"<version>{vndk_version}</version>" in original_content:
            self.logger.info(f"VNDK {vndk_version} already exists in manifest. Skipping.")
            return

        new_block = f"""    <vendor-ndk>
        <version>{vndk_version}</version>
    </vendor-ndk>"""

        if "</manifest>" in original_content:
            new_content = original_content.replace("</manifest>", f"{new_block}\n</manifest>")
            
            target_xml.write_text(new_content, encoding='utf-8')
            self.logger.info(f"Injected VNDK {vndk_version} into {target_xml.name} (Text Mode)")
        else:
            self.logger.error("Invalid manifest.xml: No </manifest> tag found.")

class FrameworkModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("FrameworkModifier")
        self.shell = ShellRunner()
        self.bin_dir = Path("bin").resolve()
        
        self.apktool_path = self.bin_dir / "apktool" / "apktool"
        self.apkeditor_path = self.bin_dir / "APKEditor.jar"
        self.baksmali_path = self.bin_dir / "baksmali.jar"
        
        self.RETRUN_TRUE = ".locals 1\n    const/4 v0, 0x1\n    return v0"
        self.RETRUN_FALSE = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        self.REMAKE_VOID = ".locals 0\n    return-void"
        self.INVOKE_TRUE = "invoke-static {}, Lcom/android/internal/util/HookHelper;->RETURN_TRUE()Z"
        self.PRELOADS_SHAREDUIDS = ".locals 1\n    invoke-static {}, Lcom/android/internal/util/HookHelper;->RETURN_TRUE()Z\n    move-result v0\n    sput-boolean v0, Lcom/android/server/pm/ReconcilePackageUtils;->ALLOW_NON_PRELOADS_SYSTEM_SHAREDUIDS:Z\n    return-void"

        self.temp_dir = self.ctx.target_dir.parent / "temp_modifier"

    def run(self):
        self.logger.info("Starting System Modification...")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            futures.append(executor.submit(self._mod_miui_services))
            futures.append(executor.submit(self._mod_services))
            futures.append(executor.submit(self._mod_framework))
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Framework modification failed: {e}")

        self._inject_xeu_toolbox()
        self.logger.info("System Modification Completed.")

    def _run_smalikit(self, **kwargs):
        args = SmaliArgs(**kwargs)
        patcher = SmaliKit(args)
        target = args.file_path if args.file_path else args.path
        if target:
            patcher.walk_and_patch(target)

    def _apkeditor_decode(self, jar_path, out_dir):
        self.shell.run_java_jar(self.apkeditor_path, ["d", "-f", "-i", str(jar_path), "-o", str(out_dir)])

    def _apkeditor_build(self, src_dir, out_jar):
        self.shell.run_java_jar(self.apkeditor_path, ["b", "-f", "-i", str(src_dir), "-o", str(out_jar)])

    def _find_file(self, root, name_pattern):
        for p in Path(root).rglob(name_pattern):
            if p.is_file(): return p
        return None

    def _replace_text_in_file(self, file_path, old, new):
        if not file_path or not file_path.exists():
            return
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        if old in content:
            new_content = content.replace(old, new)
            file_path.write_text(new_content, encoding='utf-8')
            self.logger.info(f"Patched {file_path.name}: {old[:20]}... -> {new[:20]}...")

    def _mod_miui_services(self):
        jar_path = self._find_file(self.ctx.target_dir, "miui-services.jar")
        if not jar_path: return

        self.logger.info(f"Modifying {jar_path.name}...")
        work_dir = self.temp_dir / "miui-services"
        self._apkeditor_decode(jar_path, work_dir)

        if getattr(self.ctx, "is_port_eu_rom", False):
            fuc_body = ".locals 1\n    invoke-direct {p0}, Lcom/android/server/SystemServerStub;-><init>()V\n    return-void"
            self._run_smalikit(
                path=str(work_dir),
                iname="SystemServerImpl.smali",
                method="<init>()V",
                remake=fuc_body
            )

        remake_void = ".locals 0\n    return-void"
        remake_false = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceImpl.smali", method="verifyIsolationViolation", remake=remake_void, recursive=True)
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceImpl.smali", method="canBeUpdate", remake=remake_void, recursive=True)
        
        patches = [
            ("com/android/server/am/BroadcastQueueModernStubImpl.smali", [
                ('sget-boolean v2, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v2, 0x1')
            ]),
            ("com/android/server/am/ActivityManagerServiceImpl.smali", [
                ('sget-boolean v1, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v1, 0x1'),
                ('sget-boolean v4, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v4, 0x1')
            ]),
            ("com/android/server/am/ProcessManagerService.smali", [
                ('sget-boolean v0, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v0, 0x1')
            ]),
            ("com/android/server/am/ProcessSceneCleaner.smali", [
                ('sget-boolean v4, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v0, 0x1')
            ]),
        ]

        for rel_path, rules in patches:
            target_smali = self._find_file(work_dir, Path(rel_path).name)
            if target_smali:
                for old_str, new_str in rules:
                    self._replace_text_in_file(target_smali, old_str, new_str)

        self._run_smalikit(path=str(work_dir), iname="WindowManagerServiceImpl.smali", method="notAllowCaptureDisplay(Lcom/android/server/wm/RootWindowContainer;I)Z", remake=remake_false, recursive=True)

        self._apkeditor_build(work_dir, jar_path)

    def _mod_services(self):
        jar_path = self._find_file(self.ctx.target_dir, "services.jar")
        if not jar_path: return

        self.logger.info(f"Modifying {jar_path.name}...")
        work_dir = self.temp_dir / "services"
        shutil.copy2(jar_path, self.temp_dir / "services.jar.bak")
        self._apkeditor_decode(jar_path, work_dir)

        remake_void = ".locals 0\n    return-void"
        remake_false = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        remake_true = ".locals 1\n    const/4 v0, 0x1\n    return v0"
        
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceUtils.smali", method="checkDowngrade", remake=remake_void, recursive=True)
        for m in ["matchSignaturesCompat", "matchSignaturesRecover", "matchSignatureInSystem", "verifySignatures"]:
            self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceUtils.smali", method=m, remake=remake_false)

        self._run_smalikit(path=str(work_dir), iname="KeySetManagerService.smali", method="checkUpgradeKeySetLocked", remake=remake_true)
        
        self._run_smalikit(path=str(work_dir), iname="VerifyingSession.smali", method="isVerificationEnabled", remake=remake_false)
        
        self._apkeditor_build(work_dir, jar_path)

    def _find_file_recursive(self, root_dir: Path, filename: str) -> Path | None:
        if not root_dir.exists(): return None
        try:
            return next(root_dir.rglob(filename))
        except StopIteration:
            return None

    def _find_dir_recursive(self, root_dir: Path, dirname: str) -> Path | None:
        if not root_dir.exists(): return None
        for p in root_dir.rglob(dirname):
            if p.is_dir() and p.name == dirname:
                return p
        return None

    def _mod_framework(self):
        jar = self._find_file_recursive(self.ctx.target_dir, "framework.jar")
        if not jar: return
        self.logger.info(f"Modifying {jar.name} (PropsHook, PIF & SignBypass)...")
        
        wd = self.temp_dir / "framework"
        self.shell.run_java_jar(self.apkeditor_path, ["d", "-f", "-i", str(jar), "-o", str(wd), "-no-dex-debug"])

        props_hook_zip = Path("devices/common/PropsHook.zip")
        if props_hook_zip.exists():
            self.logger.info("Injecting PropsHook...")
            hook_tmp = self.temp_dir / "PropsHook"
            with zipfile.ZipFile(props_hook_zip, 'r') as z:
                z.extractall(hook_tmp)
            
            classes_dex = hook_tmp / "classes.dex"
            if classes_dex.exists():
                classes_out = hook_tmp / "classes"
                self.shell.run_java_jar(self.baksmali_path, ["d", str(classes_dex), "-o", str(classes_out)])
                
                self._copy_to_next_classes(wd, classes_out)

        self.logger.info("Applying Signature Bypass Patches...")
        
        self._run_smalikit(path=str(wd), iname="StrictJarVerifier.smali", method="verifyMessageDigest([B[B)Z", remake=self.RETRUN_TRUE)
        self._run_smalikit(path=str(wd), iname="StrictJarVerifier.smali", 
                           method="<init>(Ljava/lang/String;Landroid/util/jar/StrictJarManifest;Ljava/util/HashMap;Z)V", 
                           before_line=["iput-boolean p4, p0, Landroid/util/jar/StrictJarVerifier;->signatureSchemeRollbackProtectionsEnforced:Z", "const/4 p4, 0x0"])

        targets = [
            ("ApkSigningBlockUtils.smali", "verifyIntegrityFor1MbChunkBasedAlgorithm"),
            ("ApkSigningBlockUtils.smali", "verifyProofOfRotationStruct"),
            ("ApkSignatureSchemeV2Verifier.smali", "verifySigner"),
            ("ApkSignatureSchemeV3Verifier.smali", "verifySigner"),
            ("ApkSignatureSchemeV4Verifier.smali", "verifySigner"),
        ]
        s1 = "Ljava/security/MessageDigest;->isEqual([B[B)Z"
        s2 = "Ljava/security/Signature;->verify([B)Z"
        
        for smali_file, method in targets:
             self._run_smalikit(path=str(wd), iname=smali_file, method=method, after_line=[s1, self.INVOKE_TRUE], recursive=True)
             self._run_smalikit(path=str(wd), iname=smali_file, method=method, after_line=[s2, self.INVOKE_TRUE], recursive=True)

        for m in ["checkCapability", "checkCapabilityRecover", "hasCommonAncestor", "signaturesMatchExactly"]:
            self._run_smalikit(path=str(wd), iname="PackageParser$SigningDetails.smali", method=m, remake=self.RETRUN_TRUE, recursive=True)
            self._run_smalikit(path=str(wd), iname="SigningDetails.smali", method=m, remake=self.RETRUN_TRUE, recursive=True)

        self._run_smalikit(path=str(wd), iname="AssetManager.smali", method="containsAllocatedTable", remake=self.RETRUN_FALSE)

        self._run_smalikit(path=str(wd), iname="StrictJarFile.smali", 
                           method="<init>(Ljava/lang/String;Ljava/io/FileDescriptor;ZZ)V", 
                           after_line=["move-result-object v6", "const/4 v6, 0x1"])

        self._run_smalikit(path=str(wd), iname="ApkSignatureVerifier.smali", method="getMinimumSignatureSchemeVersionForTargetSdk", remake=self.RETRUN_TRUE)

        pif_zip = Path("devices/common/pif_patch_v2.zip")
        if pif_zip.exists():
            self._apply_pif_patch(wd, pif_zip)
        else:
            self.logger.warning("pif_patch_v2.zip not found, skipping PIF injection.")

        target_file = self._find_file_recursive(wd, "PendingIntent.smali")
        if target_file:
            hook_code = "\n    # [AutoCopy Hook]\n    invoke-static {p0, p2}, Lcom/android/internal/util/HookHelper;->onPendingIntentGetActivity(Landroid/content/Context;Landroid/content/Intent;)V"
            self._run_smalikit(file_path=str(target_file), method="getActivity(Landroid/content/Context;ILandroid/content/Intent;I)", insert_line=["2", hook_code])
            self._run_smalikit(file_path=str(target_file), method="getActivity(Landroid/content/Context;ILandroid/content/Intent;ILandroid/os/Bundle;)", insert_line=["2", hook_code])

        self._integrate_custom_platform_key(wd)

        # ==========================================
        # 6. 注入 HookHelper 实现 (AutoCopy)
        # ==========================================
        self._inject_hook_helper_methods(wd)

        # [Patch] Fix Voice Trigger for A16 (SoundTrigger$RecognitionConfig)
        if int(self.ctx.port_android_version) >= 16:
            st_config = self._find_file_recursive(wd, "SoundTrigger$RecognitionConfig.smali")
            if st_config:
                self.logger.info(f"Applying VoiceTrigger compatibility patch to {st_config.name}...")
                content = st_config.read_text(encoding='utf-8', errors='ignore')
                
                # 1. Add field if missing
                field_def = ".field public captureRequested:Z"
                if field_def not in content:
                    target_field = ".field private final blacklist mCaptureRequested:Z"
                    if target_field in content:
                        content = content.replace(target_field, f"{target_field}\n{field_def}")
                        st_config.write_text(content, encoding='utf-8')
                        self.logger.info("  -> Added field captureRequested")
                
                # 2. Add assignment in constructor
                constructor_sig = "<init>(ZZ[Landroid/hardware/soundtrigger/SoundTrigger$KeyphraseRecognitionExtra;[BI)V"
                old_iput = "iput-boolean p1, p0, Landroid/hardware/soundtrigger/SoundTrigger$RecognitionConfig;->mCaptureRequested:Z"
                
                self._run_smalikit(
                    file_path=str(st_config),
                    method=constructor_sig,
                    after_line=[old_iput, "iput-boolean p1, p0, Landroid/hardware/soundtrigger/SoundTrigger$RecognitionConfig;->captureRequested:Z"]
                )

        self._apkeditor_build(wd, jar)

    def _inject_hook_helper_methods(self, work_dir):
        """
        注入 HookHelper 的额外方法 (AutoCopy 等)
        """
        hook_helper = self._find_file_recursive(work_dir, "HookHelper.smali")
        if not hook_helper:
            self.logger.warning("HookHelper.smali not found, creating new one...")
            return

        self.logger.info(f"Injecting implementation into {hook_helper.name}...")
        
        # 定义 Smali 代码
        smali_code = r"""
.method public static onPendingIntentGetActivity(Landroid/content/Context;Landroid/content/Intent;)V
    .locals 5

    .line 100
    if-eqz p1, :cond_end

    # Check for extras
    invoke-virtual {p1}, Landroid/content/Intent;->getExtras()Landroid/os/Bundle;
    move-result-object v0
    if-nez v0, :cond_check_clip

    goto :cond_end

    :cond_check_clip
    # Try to find "sms_body" or typical keys
    const-string v1, "android.intent.extra.TEXT"
    invoke-virtual {v0, v1}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    
    if-nez v1, :cond_check_body
    const-string v1, "sms_body"
    invoke-virtual {v0, v1}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1

    :cond_check_body
    if-nez v1, :cond_scan_match
    goto :cond_end

    :cond_scan_match
    # Now v1 is the content string. Run Regex.
    # Regex: (?<![0-9])([0-9]{4,6})(?![0-9])
    
    const-string v2, "(?<![0-9])([0-9]{4,6})(?![0-9])"
    invoke-static {v2}, Ljava/util/regex/Pattern;->compile(Ljava/lang/String;)Ljava/util/regex/Pattern;
    move-result-object v2
    invoke-virtual {v2, v1}, Ljava/util/regex/Pattern;->matcher(Ljava/lang/CharSequence;)Ljava/util/regex/Matcher;
    move-result-object v2
    
    invoke-virtual {v2}, Ljava/util/regex/Matcher;->find()Z
    move-result v3
    if-eqz v3, :cond_end
    
    # Found match! Group 1 is the code
    const/4 v3, 0x1
    invoke-virtual {v2, v3}, Ljava/util/regex/Matcher;->group(I)Ljava/lang/String;
    move-result-object v2
    
    if-eqz v2, :cond_end
    
    # Copy to Clipboard
    const-string v3, "clipboard"
    invoke-virtual {p0, v3}, Landroid/content/Context;->getSystemService(Ljava/lang/String;)Ljava/lang/Object;
    move-result-object v3
    check-cast v3, Landroid/content/ClipboardManager;
    
    if-eqz v3, :cond_end
    
    # ClipData.newPlainText("Verification Code", code)
    const-string v4, "Verification Code"
    invoke-static {v4, v2}, Landroid/content/ClipData;->newPlainText(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Landroid/content/ClipData;
    move-result-object v2
    
    invoke-virtual {v3, v2}, Landroid/content/ClipboardManager;->setPrimaryClip(Landroid/content/ClipData;)V
    
    :cond_end
    return-void
.end method
"""
        # Append method to HookHelper.smali
        content = hook_helper.read_text(encoding='utf-8')
        if "onPendingIntentGetActivity" not in content:
            with open(hook_helper, "a", encoding="utf-8") as f:
                f.write(smali_code)
                
            self.logger.info("Added onPendingIntentGetActivity to HookHelper.")
        else:
            self.logger.info("onPendingIntentGetActivity already exists.")

    # --------------------------------------------------------------------------
    # PIF Patch 逻辑 (模拟 patches.sh)
        # --------------------------------------------------------------------------
    def _apply_pif_patch(self, work_dir, pif_zip):
        self.logger.info("Applying PIF Patch (Instrumentation, KeyStoreSpi, AppPM)...")
        
        temp_pif = self.temp_dir / "pif_classes"
        with zipfile.ZipFile(pif_zip, 'r') as z:
            z.extractall(temp_pif)
        self._copy_to_next_classes(work_dir, temp_pif / "classes")
        
        self.logger.info(f"Merging files from {temp_pif} to {self.ctx.target_dir}...")
        
        for item in temp_pif.iterdir():
            if item.name == "classes":
                continue
            
            target_path = self.ctx.target_dir / item.name
            
            self.logger.info(f"  Merging: {item.name} -> {target_path}")
            
            if item.is_dir():
                shutil.copytree(item, target_path, symlinks=True, dirs_exist_ok=True)
            else:
                if target_path.exists() or os.path.islink(target_path):
                    if target_path.is_dir(): shutil.rmtree(target_path)
                    else: os.unlink(target_path)
                
                shutil.copy2(item, target_path, follow_symlinks=False)

        inst_smali = self._find_file_recursive(work_dir, "Instrumentation.smali")
        if inst_smali:
            content = inst_smali.read_text(encoding='utf-8', errors='ignore')
            
            method1 = "newApplication(Ljava/lang/ClassLoader;Ljava/lang/String;Landroid/content/Context;)Landroid/app/Application;"
            if method1 in content:
                reg = self._extract_register_from_invoke(content, method1, "Landroid/app/Application;->attach(Landroid/content/Context;)V", arg_index=1)
                if reg:
                    patch_code = f"    invoke-static {{{reg}}}, Lcom/android/internal/util/PropsHookUtils;->setProps(Landroid/content/Context;)V\n    invoke-static {{{reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onNewApplication(Landroid/content/Context;)V"
                    self._run_smalikit(file_path=str(inst_smali), method=method1, before_line=["return-object", patch_code])

            method2 = "newApplication(Ljava/lang/Class;Landroid/content/Context;)Landroid/app/Application;"
            if method2 in content:
                reg = self._extract_register_from_invoke(content, method2, "Landroid/app/Application;->attach(Landroid/content/Context;)V", arg_index=1)
                if reg:
                    patch_code = f"    invoke-static {{{reg}}}, Lcom/android/internal/util/PropsHookUtils;->setProps(Landroid/content/Context;)V\n    invoke-static {{{reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onNewApplication(Landroid/content/Context;)V"
                    self._run_smalikit(file_path=str(inst_smali), method=method2, before_line=["return-object", patch_code])
        keystore_smali = self._find_file_recursive(work_dir, "AndroidKeyStoreSpi.smali")
        if keystore_smali:
            self.logger.info("Hooking AndroidKeyStoreSpi...")
            self._run_smalikit(file_path=str(keystore_smali), method="engineGetCertificateChain", 
                               insert_line=["2", "    invoke-static {}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onEngineGetCertificateChain()V"])
   
        # New hooks from patchframework.sh (KeyStore2 and KeyStoreSecurityLevel)
        keystore2_smali = self._find_file_recursive(work_dir, "KeyStore2.smali")
        if keystore2_smali:
            self.logger.info("Hooking KeyStore2...")
            content = keystore2_smali.read_text(encoding='utf-8')
            
            # 1. onDeleteKey hook
            delete_key_name = "deleteKey"
            reg = self._extract_register_from_local(content, delete_key_name, '"descriptor"') or "p1"
            
            # Use \$+ to match one or more literal '$' signs (e.g., $ or $$)
            on_delete_patch = rf"    invoke-static {{{reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onDeleteKey(Landroid/system/keystore2/KeyDescriptor;)V\n\n    \1"
            self._run_smalikit(file_path=str(keystore2_smali), method=delete_key_name, 
                               regex_replace=(r"(new-instance\s+.*?, Landroid/security/KeyStore2\$+ExternalSyntheticLambda.*)", on_delete_patch))

            # 2. onGetKeyEntry hook
            get_key_entry_name = "getKeyEntry"
            reg = self._extract_register_from_local(content, get_key_entry_name, '"descriptor"') or "p1"
            
            on_get_key_patch = rf"    invoke-static {{p0, v0, {reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onGetKeyEntry(Ljava/lang/Object;Ljava/lang/Object;Landroid/system/keystore2/KeyDescriptor;)Landroid/system/keystore2/KeyEntryResponse;\n    move-result-object {reg}\n    if-eqz {reg}, :cond_skip_spoofing\n    return-object {reg}\n    :cond_skip_spoofing\n\n    \1"
            self._run_smalikit(file_path=str(keystore2_smali), method=get_key_entry_name,
                               regex_replace=(r"(invoke-virtual\s+.*?, Landroid/security/KeyStore2;->handleRemoteExceptionWithRetry.*)", on_get_key_patch))

        keystore_lvl_smali = self._find_file_recursive(work_dir, "KeyStoreSecurityLevel.smali")
        if keystore_lvl_smali:
            self.logger.info("Hooking KeyStoreSecurityLevel...")
            content = keystore_lvl_smali.read_text(encoding='utf-8')
            gen_key_name = "generateKey"
            
            # Find the method body to extract registers used in Lambda init
            method_pattern = re.compile(rf"\.method[^\n]*?{gen_key_name}(.*?)\.end method", re.DOTALL)
            m = method_pattern.search(content)
            
            desc_reg, args_reg, ret_reg = "p1", "p3", "v0" # Safe defaults
            
            if m:
                body = m.group(1)
                # 1. Extract registers from invoke-direct/range {v0 .. vX}
                # In your code: invoke-direct/range {v0 .. v6}, ...KeyStoreSecurityLevel$$ExternalSyntheticLambda2;-><init>
                range_match = re.search(r"invoke-direct\/range\s+{(?P<start>[vp]\d+)\s+\.\.\s+(?P<end>[vp]\d+)}", body)
                if range_match:
                    start_reg = range_match.group("start") # e.g., v0
                    start_prefix = start_reg[0]
                    start_num = int(start_reg[1:])
                    
                    # Based on your smali: v0=lambda, v1=this, v2=descriptor(p1), v4=args(p3)
                    desc_reg = f"{start_prefix}{start_num + 2}" # v0 + 2 = v2
                    args_reg = f"{start_prefix}{start_num + 4}" # v0 + 4 = v4
                    self.logger.info(f"  -> Extracted registers from range: desc={desc_reg}, args={args_reg}")
                
                # 2. Extract return register
                ret_match = re.search(r"return-object\s+([vp]\d+)", body)
                if ret_match: ret_reg = ret_match.group(1)

            gen_cert_patch = rf"    invoke-static {{p0, v0, {desc_reg}, {args_reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->genCertificate(Ljava/lang/Object;Ljava/lang/Object;Landroid/system/keystore2/KeyDescriptor;Ljava/util/Collection;)Landroid/system/keystore2/KeyMetadata;\n    move-result-object {ret_reg}\n    if-eqz {ret_reg}, :cond_skip_spoofing\n    return-object {ret_reg}\n    :cond_skip_spoofing\n\n    \1"
            self._run_smalikit(file_path=str(keystore_lvl_smali), method=gen_key_name,
                               regex_replace=(r"(invoke-direct\s+.*?, Landroid/security/KeyStoreSecurityLevel;->handleExceptions.*)", gen_cert_patch))

        app_pm_smali = self._find_file_recursive(work_dir, "ApplicationPackageManager.smali")
        if app_pm_smali:
            self.logger.info("Hooking ApplicationPackageManager...")
            
            method_sig = "hasSystemFeature(Ljava/lang/String;I)Z"
            
            repl_pattern = (
                r"invoke-static {p1, \1}, Lcom/android/internal/util/PropsHookUtils;->hasSystemFeature(Ljava/lang/String;Z)Z"
                r"\n    move-result \1"
                r"\n    return \1"
            )
            
            self._run_smalikit(
                file_path=str(app_pm_smali), 
                method=method_sig, 
                regex_replace=(r"return\s+([vp]\d+)", repl_pattern)
            )
        
        policy_tool = self.bin_dir / "insert_selinux_policy.py"
        config_json = Path("devices/common/pif_updater_policy.json")
        cil_path = self.ctx.target_dir / "system/system/etc/selinux/plat_sepolicy.cil"
        
        if policy_tool.exists() and config_json.exists() and cil_path.exists():
            self.shell.run(["python3", str(policy_tool), "--config", str(config_json), str(cil_path)])
            
            fc_path = self.ctx.target_dir / "system/system/etc/selinux/plat_file_contexts"
            if fc_path.exists():
                with open(fc_path, "a") as f:
                    f.write("\n/system/bin/pif-updater       u:object_r:pif_updater_exec:s0\n")
                    f.write("/data/system/pif_tmp.apk  u:object_r:pif_data_file:s0\n")
                    f.write("/data/PIF.apk u:object_r:pif_data_file:s0\n")
                    f.write("/data/local/tmp/PIF.apk   u:object_r:pif_data_file:s0\n")
        
        # Properties migrated to devices/common/features.json

    # --------------------------------------------------------------------------
    # 自定义平台签名校验逻辑
    # --------------------------------------------------------------------------
    def _integrate_custom_platform_key(self, work_dir):
        epm_smali = self._find_file_recursive(work_dir, "ExtraPackageManager.smali")
        if not epm_smali: return
        self.logger.info("Injecting Custom Platform Key Check...")

        MY_PLATFORM_KEY = "308203bb308202a3a00302010202146a0b4f6a1a8f61a32d8450ead92d479dea486573300d06092a864886f70d01010b0500306c310b300906035504061302434e3110300e06035504080c075369436875616e3110300e06035504070c074368656e6744753110300e060355040a0c07504f5254524f4d31133011060355040b0c0a4d61696e7461696e65723112301006035504030c09427275636554656e673020170d3236303230323031333632385a180f32303533303632303031333632385a306c310b300906035504061302434e3110300e06035504080c075369436875616e3110300e06035504070c074368656e6744753110300e060355040a0c07504f5254524f4d31133011060355040b0c0a4d61696e7461696e65723112301006035504030c09427275636554656e6730820122300d06092a864886f70d01010105000382010f003082010a0282010100cb68bcf8927a175624a0a7428f1bbd67b4cf18c8ba42b73de9649fd2aa42935b9195b27ccd611971056654db51499ffa01783a1dbc95e03f9c557d4930193c3d04f9016a84411b502ea844fac9d463b4c9eed2d73ca3267b8a399f5da254941c7413d2a7534fd30a4ed10567933bfda249e2027ce74da667de3b6278844d232e038c2c98deb7d172a44b2fd9ec90ea74cb1c96b647044c60ce18cec93b60b84065ddd8800e10bcf465e4f3ace6d423ef2b235d75081e36b5d0f1ca858090d3dd8d74437ebb504490a8e7e9e3e2b696c3ac8e2ec856bedf4efe4e05e14f2437f81fbc8428aa330cdde0816450b4416e10f743204c17ee65b92ebc61799b4cf42b0203010001a3533051301d0603551d0e041604140a318d86cc0040341341b6dc716094da06cd4dd6301f0603551d230418301680140a318d86cc0040341341b6dc716094da06cd4dd6300f0603551d130101ff040530030101ff300d06092a864886f70d01010b0500038201010023e7aeda5403f40c794504e3edf99182a5eb53c9ddec0d93fd9fe6539e1520ea6ad08ac3215555f3fe366fa6ab01e0f45d6ce1512416c572f387a72408dde6442b76e405296cc8c128844fe68a29f6a114eb6f303e3545ea0b32d85e9c7d45cfa3c860b03d00171bb2aa4434892bf484dd390643f324a2e38a5e6ce7f26e92b3d02ac8605514b9c75a8aab9ab990c01951213f7214a36389c0759cfb68737bb3bb85dff4b1b40377279e2c82298351c276ab266869d6494b838bd6cc175185f705b8806eb1950becec57fb4f9b50240bb92d1d30bbb5764d311d18446588e5fd2b9785c635f2bb690df1e4fb595305371350c6d306d3f6cae3bc4974e9d8609c"
        
        hook_code = f"""
    # [Start] Custom Platform Key Check
    const/4 v2, 0x1
    new-array v2, v2, [Landroid/content/pm/Signature;
    new-instance v3, Landroid/content/pm/Signature;
    const-string v4, "{MY_PLATFORM_KEY}"
    invoke-direct {{v3, v4}}, Landroid/content/pm/Signature;-><init>(Ljava/lang/String;)V
    const/4 v4, 0x0
    aput-object v3, v2, v4
    invoke-static {{p0, v2}}, Lmiui/content/pm/ExtraPackageManager;->compareSignatures([Landroid/content/pm/Signature;[Landroid/content/pm/Signature;)I
    move-result v2
    if-eqz v2, :cond_custom_skip
    const/4 v2, 0x1
    return v2
    :cond_custom_skip
    # [End]"""

        self._run_smalikit(file_path=str(epm_smali), method="isTrustedPlatformSignature([Landroid/content/pm/Signature;)Z", 
                           regex_replace=(r"\.locals\s+\d+", ".locals 5"))
        
        self._run_smalikit(file_path=str(epm_smali), method="isTrustedPlatformSignature([Landroid/content/pm/Signature;)Z", 
                           insert_line=["2", hook_code])

    def _copy_to_next_classes(self, work_dir, source_dir):
        max_num = 1
        for d in work_dir.glob("smali/classes*"):
             name = d.name
             if name == "classes": num = 1
             else: 
                 try: num = int(name.replace("classes", ""))
                 except: num = 1
             if num > max_num: max_num = num
        
        target = work_dir / "smali" / f"classes{max_num + 1}"
        shutil.copytree(source_dir, target, dirs_exist_ok=True)
        self.logger.info(f"Copied classes to {target.name}")

    def _extract_register_from_invoke(self, content: str, method_signature: str, invoke_signature: str, arg_index: int = 1) -> str:
        method_pattern = re.compile(
            rf"\.method[^\n]*?{re.escape(method_signature)}(.*?)\.end method", 
            re.DOTALL
        )
        method_match = method_pattern.search(content)
        
        if not method_match:
            self.logger.warning(f"Target method not found: {method_signature}")
            return None
            
        method_body = method_match.group(1)

        invoke_pattern = re.compile(
            rf"invoke-\w+\s+{{(.*?)}},\s+{re.escape(invoke_signature)}"
        )
        invoke_match = invoke_pattern.search(method_body)
        
        if not invoke_match:
            self.logger.warning(f"Invoke signature not found in method body: {invoke_signature}")
            return None
            
        matched_regs_str = invoke_match.group(1)
        
        reg_list = [r.strip() for r in matched_regs_str.split(',') if r.strip()]
        
        if arg_index < len(reg_list):
            extracted_reg = reg_list[arg_index]
            self.logger.debug(f"Extracted register {extracted_reg} from {method_signature}")
            return extracted_reg
        else:
            self.logger.warning(f"arg_index {arg_index} out of bounds for registers: {reg_list}")
            return None

    def _extract_register_from_local(self, content: str, method_signature: str, local_name: str) -> str | None:
        """
        Extract register name from .local declaration or move-object instructions.
        """
        method_pattern = re.compile(
            rf"\.method[^\n]*?{re.escape(method_signature)}(.*?)\.end method", 
            re.DOTALL
        )
        method_match = method_pattern.search(content)
        if not method_match:
            return None
            
        body = method_match.group(1)
        
        # 1. Try .local declaration first
        # Match .local reg, "name":TYPE or .local reg, "name", TYPE
        local_pattern = re.compile(rf'\.local\s+([vp]\d+),\s+{re.escape(local_name)}[;:,]')
        match = local_pattern.search(body)
        if match:
            return match.group(1)
            
        # 2. Fallback: If it's "descriptor" or "args", try to find move-object from p1/p3
        # This is common in optimized dex where params are moved to locals
        if local_name == '"descriptor"':
            # Match move-object v2, p1
            move_match = re.search(r"move-object(?:\/from16)?\s+([vp]\d+),\s+p1", body)
            if move_match: return move_match.group(1)
        elif local_name == '"args"':
            # Match move-object v4, p3
            move_match = re.search(r"move-object(?:\/from16)?\s+([vp]\d+),\s+p3", body)
            if move_match: return move_match.group(1)
            
        return None

    def _inject_xeu_toolbox(self):
        xeu_zip = Path("devices/common/xeutoolbox.zip")
        if not xeu_zip.exists():
            return

        self.logger.info("Injecting Xiaomi.eu Toolbox...")

        try:
            with zipfile.ZipFile(xeu_zip, 'r') as z:
                z.extractall(self.ctx.target_dir)
            self.logger.info(f"Extracted {xeu_zip.name}")
        except Exception as e:
            self.logger.error(f"Failed to extract xeutoolbox: {e}")
            return

        target_files = [
            self.ctx.target_dir / "config/system_ext_file_contexts",
            self.ctx.target_dir / "system_ext/etc/selinux/system_ext_file_contexts"
        ]
        
        context_line = "\n/system_ext/xbin/xeu_toolbox  u:object_r:toolbox_exec:s0\n"

        for f in target_files:
            if f.exists():
                try:
                    with open(f, "a", encoding="utf-8") as file:
                        file.write(context_line)
                    self.logger.info(f"Updated contexts: {f.name}")
                except Exception as e:
                    self.logger.warning(f"Failed to append context to {f}: {e}")

        cil_file = self.ctx.target_dir / "system_ext/etc/selinux/system_ext_sepolicy.cil"
        policy_line = "\n(allow init toolbox_exec (file ((execute_no_trans))))\n"
        
        if cil_file.exists():
            try:
                with open(cil_file, "a", encoding="utf-8") as f:
                    f.write(policy_line)
                self.logger.info(f"Updated sepolicy: {cil_file.name}")
            except Exception as e:
                self.logger.warning(f"Failed to append policy to {cil_file}: {e}")
                
class FirmwareModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("FirmwareMod")
        self.shell = ShellRunner()
        self.bin_dir = Path("bin").resolve()
        
        if not self.ctx.tools.magiskboot.exists():
            self.logger.error(f"magiskboot binary not found at {self.ctx.tools.magiskboot}")
            return
        
        self.assets_dir = self.bin_dir.parent / "assets"
        self.ksu_version_file = self.assets_dir / "ksu_version.txt"
        self.repo_owner = "tiann"
        self.repo_name = "KernelSU"

    def run(self):
        self.logger.info("Starting Firmware Modification...")
        
        self._patch_vbmeta()
        
        if getattr(self.ctx, "enable_ksu", False):
            self._patch_ksu()
        
        self.logger.info("Firmware Modification Completed.")

    def _patch_vbmeta(self):
        self.logger.info("Patching vbmeta images (Disabling AVB)...")
        
        vbmeta_images = list(self.ctx.target_dir.rglob("vbmeta*.img"))
        
        if not vbmeta_images:
            self.logger.warning("No vbmeta images found in target directory.")
            return

        AVB_MAGIC = b"AVB0"
        FLAGS_OFFSET = 123
        FLAGS_TO_SET = b'\x03'

        for img_path in vbmeta_images:
            try:
                with open(img_path, "r+b") as f:
                    magic = f.read(4)
                    if magic != AVB_MAGIC:
                        self.logger.warning(f"Skipping {img_path.name}: Invalid AVB Magic")
                        continue
                    
                    f.seek(FLAGS_OFFSET)
                    f.write(FLAGS_TO_SET)
                    self.logger.info(f"Successfully patched: {img_path.name}")
                    
            except Exception as e:
                self.logger.error(f"Failed to patch {img_path.name}: {e}")

    def _patch_ksu(self):
        self.logger.info("Attempting to patch KernelSU...")
        
        target_init_boot = self.ctx.target_dir / "repack_images" / "init_boot.img"
        target_boot = self.ctx.target_dir / "repack_images" / "boot.img"
        
        patch_target = None
        if target_init_boot.exists():
            patch_target = target_init_boot
        elif target_boot.exists():
            patch_target = target_boot
        
        if not patch_target:
            self.logger.warning("Neither init_boot.img nor boot.img found, skipping KSU patch.")
            return
            
        if not self.ctx.tools.magiskboot.exists():
            self.logger.error("magiskboot binary not found!")
            return

        # Kernel is usually in boot.img for GKI, or in the target image itself for non-GKI.
        kmi_version = self._analyze_kmi(target_boot if target_boot.exists() else patch_target)
        if not kmi_version:
            self.logger.error("Failed to determine KMI version.")
            return
        
        self.logger.info(f"Detected KMI Version: {kmi_version}")

        if not self._prepare_ksu_assets(kmi_version):
            self.logger.error("Failed to prepare KSU assets.")
            return
            
        self._apply_ksu_patch(patch_target, kmi_version)

    def _analyze_kmi(self, boot_img):
        with tempfile.TemporaryDirectory(prefix="ksu_kmi_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(boot_img, tmp_path / "boot.img")
            
            try:
                self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path)
            except Exception:
                return None
            
            kernel_file = tmp_path / "kernel"
            if not kernel_file.exists(): return None
            
            try:
                with open(kernel_file, 'rb') as f:
                    content = f.read()
                    
                strings = []
                current = []
                for b in content:
                    if 32 <= b <= 126:
                        current.append(chr(b))
                    else:
                        if len(current) >= 4: strings.append("".join(current))
                        current = []
                
                pattern = re.compile(r'(?:^|\s)(\d+\.\d+)\S*(android\d+)')
                for s in strings:
                    if "Linux version" in s or "android" in s:
                        match = pattern.search(s)
                        if match:
                            return f"{match.group(2)}-{match.group(1)}"
            except Exception:
                pass
        return None

    def _prepare_ksu_assets(self, kmi_version):
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        
        target_ko = self.assets_dir / f"{kmi_version}_kernelsu.ko"
        target_init = self.assets_dir / "ksuinit"
        
        if target_ko.exists() and target_init.exists():
            return True
            
        self.logger.info("Downloading KernelSU assets...")
        try:
            api_url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
            with urllib.request.urlopen(api_url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                
            assets = data.get("assets", [])
            
            for asset in assets:
                name = asset["name"]
                url = asset["browser_download_url"]
                
                if name == "ksuinit" and not target_init.exists():
                    self._download_file(url, target_init)
                elif name == f"{kmi_version}_kernelsu.ko" and not target_ko.exists():
                    self._download_file(url, target_ko)
            
            return (target_ko.exists() and target_init.exists())
            
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return False

    def _download_file(self, url, dest):
        self.logger.info(f"Downloading {dest.name}...")
        with urllib.request.urlopen(url) as remote, open(dest, 'wb') as local:
            shutil.copyfileobj(remote, local)

    def _apply_ksu_patch(self, target_img, kmi_version):
        self.logger.info(f"Patching {target_img.name} with KernelSU...")
        
        ko_file = self.assets_dir / f"{kmi_version}_kernelsu.ko"
        init_file = self.assets_dir / "ksuinit"
        
        with tempfile.TemporaryDirectory(prefix="ksu_patch_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(target_img, tmp_path / "boot.img")
            
            self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path)
            
            ramdisk = tmp_path / "ramdisk.cpio"
            if not ramdisk.exists():
                self.logger.error("ramdisk.cpio not found")
                return

            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "mv init init.real"], cwd=tmp_path)
            
            shutil.copy(init_file, tmp_path / "init")
            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "add 0755 init init"], cwd=tmp_path)
            
            shutil.copy(ko_file, tmp_path / "kernelsu.ko")
            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "add 0755 kernelsu.ko kernelsu.ko"], cwd=tmp_path)

            self.shell.run([str(self.ctx.tools.magiskboot), "repack", "boot.img"], cwd=tmp_path)
            
            new_img = tmp_path / "new-boot.img"
            if new_img.exists():
                shutil.move(new_img, target_img)
                self.logger.info(f"KernelSU injected successfully into {target_img.name}.")
            else:
                self.logger.error(f"Failed to repack {target_img.name}")

class RomModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("RomModifier")
        
        self.stock_rom_img = self.ctx.stock_rom_dir
        self.target_rom_img = self.ctx.target_rom_dir

    def run_all_modifications(self):
        self.logger.info("=== Starting ROM Modification Phase ===")

        self._sync_and_patch_components()
        self._apply_overrides()
        
        self.logger.info("=== Modification Phase Completed ===")

    def _clean_bloatware(self):
        self.logger.info("Step 1: Cleaning Bloatware...")
        debloat_list = [
            "MSA", "AnalyticsCore", "MiuiDaemon", "MiuiBugReport", 
            "MiBrowserGlobal", "MiDrop", "XiaomiVip", "libbugreport.so"
        ]
        clean_rules = [{"mode": "delete", "target": item} for item in debloat_list]
        
        self.ctx.syncer.execute_rules(None, self.target_rom_img, clean_rules)

    def _sync_and_patch_components(self):
        self.logger.info("Step 2: Syncing Stock Components & Patching (via replacements.json)...")
        # Most components are now handled via replacements.json in SystemModifier phase.
        self.logger.info("Phase 2 sync completed.")
     
    def _apply_overrides(self):
        self.logger.info("Step 3: Applying Physical Overrides...")
        
        # 1. Common Overrides for OS3+ (LyraSdkApp fix)
        self._apply_common_overrides()

        # 2. Device Specific Overrides
        override_dir = Path(f"devices/{self.ctx.stock_rom_code}/override/{self.ctx.port_android_version}")
        self.ctx.syncer.apply_override(override_dir, self.target_rom_img)

    def _apply_common_overrides(self):
        """
        Apply common overrides based on conditions (e.g., OS version)
        """
        # Check for OS3.0+
        # ro.mi.os.version.name usually looks like "OS1.0.5.0.UMCCNXM" or "V14.0.23..."
        # But HyperOS 2.0/3.0 might be simpler in this property or need parsing.
        # User said: ro.mi.os.version.name=OS3.0
        
        os_version_name = self.ctx.port.get_prop("ro.mi.os.version.name", "")
        self.logger.info(f"Checking for common overrides. Port OS Version: {os_version_name}")
        
        if os_version_name.startswith("OS3"):
            self.logger.info("Detected HyperOS 3.0+, applying common OS3 fixes...")
            common_os3_dir = Path("devices/common/override/os3")
            if common_os3_dir.exists():
                self.ctx.syncer.apply_override(common_os3_dir, self.target_rom_img)
            else:
                self.logger.warning(f"Common OS3 override directory not found at {common_os3_dir}")
