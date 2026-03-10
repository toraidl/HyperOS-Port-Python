"""Wild Boost plugin.

This plugin installs and configures wild_boost performance modules.
"""

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List

from src.core.modifiers.plugin_system import ModifierPlugin, ModifierRegistry
from src.utils.download import AssetDownloader


@ModifierRegistry.register
class WildBoostPlugin(ModifierPlugin):
    """Plugin to install and configure wild_boost performance modules."""

    name = "wild_boost"
    description = "Install wild_boost kernel modules and apply device spoofing"
    priority = 10  # Run early

    def __init__(self, context, **kwargs):
        super().__init__(context, **kwargs)
        self.downloader = AssetDownloader()
        self.shell = None  # Will be initialized on demand

    def check_prerequisites(self) -> bool:
        """Check if wild_boost is enabled in config."""
        return self.get_config("wild_boost", {}).get("enable", False)

    def modify(self) -> bool:
        """Execute wild_boost installation."""
        from src.utils.shell import ShellRunner

        self.shell = ShellRunner()

        self.logger.info("Wild Boost is enabled...")

        # 1. Install kernel modules
        if not self._install_kernel_modules():
            self.logger.error("Failed to install kernel modules")
            return False

        # 2. Apply HexPatch to libmigui.so
        hexpatch_success = self._apply_libmigui_hexpatch()

        # 3. Fallback: Add persist.sys.feas.enable=true
        if not hexpatch_success:
            self.logger.info("Adding persist.sys.feas.enable=true as fallback...")
            self._add_feas_property()

        return True

    def _get_kernel_version(self) -> str:
        """
        Detect kernel/KMI version from boot image.
        Prioritizes full KMI string (e.g., android14-5.15) over simple version (5.15).
        """
        boot_img = self.ctx.repack_images_dir / "boot.img"
        if not boot_img.exists():
            return "unknown"

        kmi = self._analyze_kmi(boot_img)
        if kmi:
            self.logger.info(f"Detected full KMI version: {kmi}")
            return kmi
        return "unknown"

    def _analyze_kmi(self, boot_img: Path) -> str:
        """Analyze kernel image to extract KMI version (e.g., android14-5.15)."""
        from src.utils.shell import ShellRunner

        # Ensure shell is initialized
        if self.shell is None:
            self.shell = ShellRunner()

        with tempfile.TemporaryDirectory(prefix="ksu_kmi_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(boot_img, tmp_path / "boot.img")

            try:
                self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path)
            except Exception:
                return ""

            kernel_file = tmp_path / "kernel"
            if not kernel_file.exists():
                return ""

            try:
                with open(kernel_file, "rb") as f:
                    content = f.read()

                # Extract strings from binary
                strings = []
                current = []
                for b in content:
                    if 32 <= b <= 126:
                        current.append(chr(b))
                    else:
                        if len(current) >= 4:
                            strings.append("".join(current))
                        current = []

                # Pattern for KMI detection
                # Examples: "5.10.101-android12-9", "5.15.78-android14-11"
                pattern = re.compile(r"(?:^|\s)(\d+\.\d+)\S*(android\d+)")
                for s in strings:
                    if "Linux version" in s or "android" in s:
                        match = pattern.search(s)
                        if match:
                            # Return in standard KMI format: android14-5.15
                            return f"{match.group(2)}-{match.group(1)}"
            except Exception:
                pass
        return ""

    def _install_kernel_modules(self, custom_source: Path = None) -> bool:
        """Install wild_boost kernel modules with KMI matching."""
        kmi_version = self._get_kernel_version()
        if kmi_version == "unknown":
            self.logger.error("Cannot detect kernel version, wild_boost modules skipped.")
            return False

        # Extract main version (e.g., 5.15) for fallback
        main_version = ""
        version_match = re.search(r"(\d+\.\d+)", kmi_version)
        if version_match:
            main_version = version_match.group(1)

        # Determine search paths
        zip_dir = custom_source.parent if custom_source else Path("devices/common")
        base_name = custom_source.stem if custom_source else "wild_boost"

        # Multi-level matching strategy
        # 1. Full KMI match (wild_boost_android14-5.15.zip)
        # 2. Main version match (wild_boost_5.15.zip)
        candidates = [
            zip_dir / f"{base_name}_{kmi_version}.zip",
            zip_dir / f"{base_name}_{main_version}.zip",
        ]

        matching_zip = None
        for cand in candidates:
            if cand.exists():
                matching_zip = cand
                break

        if not matching_zip:
            self.logger.error(f"No matching wild_boost package found for {kmi_version}")
            self.logger.error(f"Searched for: {[c.name for c in candidates]}")
            return False

        self.logger.info(f"Using wild_boost package: {matching_zip.name} (match for {kmi_version})")

        with tempfile.TemporaryDirectory(prefix="wild_boost_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            try:
                with zipfile.ZipFile(matching_zip, "r") as z:
                    z.extractall(tmp_path)
            except Exception as e:
                self.logger.error(f"Failed to extract wild_boost zip: {e}")
                return False

            ko_files = list(tmp_path.rglob("*.ko"))
            if not ko_files:
                self.logger.error("No kernel modules (*.ko) found in zip.")
                return False

            self.logger.info(f"Found {len(ko_files)} modules: {[f.name for f in ko_files]}")

            # Auto-detect installation location
            vendor_dlkm_dir = self.ctx.target_dir / "vendor_dlkm"

            if any(v in kmi_version for v in ["5.10", "6.12"]):
                return self._install_vendor_boot(ko_files)
            elif vendor_dlkm_dir.exists():
                return self._install_vendor_dlkm(ko_files)
            else:
                self.logger.error("No suitable location (vendor_boot/vendor_dlkm) found.")
                return False

    def _install_vendor_boot(self, ko_files: List[Path]) -> bool:
        """Install wild_boost modules to vendor_boot ramdisk."""
        self.logger.info(f"Installing {len(ko_files)} modules to vendor_boot ramdisk...")

        vendor_boot_img = self.ctx.repack_images_dir / "vendor_boot.img"
        if not vendor_boot_img.exists():
            self.logger.error("vendor_boot.img not found.")
            return False

        # Create temp directory for unpacking
        work_dir = self.ctx.target_dir.parent / "temp" / "vendor_boot_work"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vendor_boot_img, work_dir / "vendor_boot.img")

        try:
            # Unpack
            self.shell.run(
                [str(self.ctx.tools.magiskboot), "unpack", "vendor_boot.img"], cwd=work_dir
            )
            ramdisk_cpio = work_dir / "ramdisk.cpio"

            # Decompress
            self.shell.run(
                [
                    str(self.ctx.tools.magiskboot),
                    "decompress",
                    "ramdisk.cpio",
                    "ramdisk.cpio.decomp",
                ],
                cwd=work_dir,
            )
            if (work_dir / "ramdisk.cpio.decomp").exists():
                ramdisk_cpio.unlink()
                (work_dir / "ramdisk.cpio.decomp").rename(ramdisk_cpio)

            # Extract to find paths
            self.shell.run(
                [str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "extract"], cwd=work_dir
            )

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
                self.shell.run(
                    [
                        str(self.ctx.tools.magiskboot),
                        "cpio",
                        "ramdisk.cpio",
                        f"add 0644 {dest_path} {ko_file}",
                    ],
                    cwd=work_dir,
                )

            # 2. Update modules.load*
            for load_file in modules_load_files:
                load_rel = load_file.relative_to(work_dir)
                content = load_file.read_text(errors="ignore")
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
                    self.shell.run(
                        [
                            str(self.ctx.tools.magiskboot),
                            "cpio",
                            "ramdisk.cpio",
                            f"add 0644 {load_rel} {load_file}",
                        ],
                        cwd=work_dir,
                    )

            # 3. Update modules.dep
            dep_file = work_dir / modules_dir_rel / "modules.dep"
            if dep_file.exists():
                self.logger.info("  Updating modules.dep...")
                content = dep_file.read_text(errors="ignore")
                lines = content.splitlines()

                # Dynamic dependency line generation for perfmgr.ko
                prefix = (
                    f"/{modules_dir_rel}/"
                    if str(modules_dir_rel).startswith("lib")
                    else "lib/modules/"
                )

                # Check kernel version for specific dependency chain
                kmi = self._get_kernel_version()
                if "6.12" in kmi:  # fixme not working
                    perfmgr_deps = [
                        "qcom-dcvs.ko",
                        "dcvs_fp.ko",
                        "qcom_rpmh.ko",
                        "cmd-db.ko",
                        "crm-v2.ko",
                        "qcom_ipc_logging.ko",
                        "minidump.ko",
                        "smem.ko",
                        "qcom_dma_heaps.ko",
                        "deferred-free-helper.ko",
                        "msm_dma_iommu_mapping.ko",
                        "mem_buf_dev.ko",
                        "gh_mem_notifier.ko",
                        "gh_rm_heap_manager.ko",
                        "mem-prot.ko",
                        "secure_buffer.ko",
                        "qcom-scm.ko",
                        "qcom_tzmem.ko",
                        "gh_rm_drv.ko",
                        "gh_msgq.ko",
                        "gh_dbl.ko",
                        "gh_arm_drv.ko",
                        "debug_symbol.ko",
                    ]
                else:  # Default/5.10
                    perfmgr_deps = [
                        "qcom-dcvs.ko",
                        "dcvs_fp.ko",
                        "qcom_rpmh.ko",
                        "cmd-db.ko",
                        "qcom_ipc_logging.ko",
                        "minidump.ko",
                        "smem.ko",
                        "sched-walt.ko",
                        "qcom-cpufreq-hw.ko",
                        "metis.ko",
                        "mi_schedule.ko",
                    ]

                perfmgr_dep_line = f"{prefix}perfmgr.ko: " + " ".join(
                    [f"{prefix}{d}" for d in perfmgr_deps]
                )

                new_lines = []
                perfmgr_found = False
                for line in lines:
                    if "perfmgr.ko:" in line:
                        new_lines.append(perfmgr_dep_line)
                        perfmgr_found = True
                    else:
                        new_lines.append(line)

                if not perfmgr_found:
                    new_lines.append(perfmgr_dep_line)

                dep_file.write_text("\n".join(new_lines) + "\n")
                self.shell.run(
                    [
                        str(self.ctx.tools.magiskboot),
                        "cpio",
                        "ramdisk.cpio",
                        f"add 0644 {dep_file.relative_to(work_dir)} {dep_file}",
                    ],
                    cwd=work_dir,
                )

            # 4. Repack
            self.logger.info("Repacking vendor_boot.img...")
            self.shell.run(
                [str(self.ctx.tools.magiskboot), "repack", "vendor_boot.img"], cwd=work_dir
            )

            new_img = work_dir / "new-boot.img"
            if new_img.exists():
                shutil.copy2(new_img, vendor_boot_img)
                self.logger.info("vendor_boot.img updated successfully.")
            else:
                self.logger.error("Failed to repack vendor_boot.img - no output file found.")
                return False

            return True
        except Exception as e:
            self.logger.error(f"Error installing to vendor_boot: {e}")
            return False
        finally:
            if work_dir.exists():
                shutil.rmtree(work_dir)

    def _install_vendor_dlkm(self, ko_files: List[Path]) -> bool:
        """Install wild_boost modules to vendor_dlkm."""
        self.logger.info(f"Installing {len(ko_files)} modules to vendor_dlkm...")

        target_dir = self.ctx.target_dir / "vendor_dlkm" / "lib" / "modules"
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Copy modules
            for ko_file in ko_files:
                dest_ko = target_dir / ko_file.name
                self.logger.info(f"  Copying {ko_file.name} to {dest_ko}")
                shutil.copy2(ko_file, dest_ko)

            # 2. Update modules.load
            modules_load = target_dir / "modules.load"
            if modules_load.exists():
                content = modules_load.read_text(encoding="utf-8", errors="ignore")
                lines = content.splitlines()
                modified = False
                for ko_file in ko_files:
                    if ko_file.name not in content:
                        lines.append(ko_file.name)
                        modified = True
                if modified:
                    modules_load.write_text("\n".join(lines) + "\n", encoding="utf-8")
            else:
                modules_load.write_text(
                    "\n".join([f.name for f in ko_files]) + "\n", encoding="utf-8"
                )

            # 3. Update modules.dep
            modules_dep = target_dir / "modules.dep"
            dep_prefix = "/vendor/lib/modules/"
            if modules_dep.exists():
                content = modules_dep.read_text(encoding="utf-8", errors="ignore")
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

                modules_dep.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            else:
                # If no dep file, at least create the entry for perfmgr
                modules_dep.write_text(f"{dep_prefix}perfmgr.ko:\n", encoding="utf-8")

            self.logger.info("wild_boost installation completed for vendor_dlkm.")
            return True
        except Exception as e:
            self.logger.error(f"Error installing to vendor_dlkm: {e}")
            return False

    def _apply_libmigui_hexpatch(self) -> bool:
        """Apply HexPatch to libmigui.so for device spoofing."""
        self.logger.info("Applying HexPatch to libmigui.so...")

        target_dir = self.ctx.target_dir
        libmigui_files = list(target_dir.rglob("libmigui.so"))

        if not libmigui_files:
            self.logger.debug("libmigui.so not found, HexPatch skipped.")
            return False

        patches = [
            {
                "old": bytes.fromhex("726F2E70726F647563742E70726F647563742E6E616D65"),
                "new": bytes.fromhex("726F2E70726F647563742E73706F6F6665642E6E616D65"),
            },
            {
                "old": bytes.fromhex("726F2E70726F647563742E646576696365"),
                "new": bytes.fromhex("726F2E73706F6F6665642E646576696365"),
            },
        ]

        patched_count = 0
        for libmigui in libmigui_files:
            try:
                content = libmigui.read_bytes()
                modified = False

                for patch in patches:
                    if patch["old"] in content:
                        content = content.replace(patch["old"], patch["new"])
                        modified = True

                if modified:
                    libmigui.write_bytes(content)
                    patched_count += 1
            except Exception as e:
                self.logger.error(f"Failed to patch {libmigui}: {e}")

        self.logger.info(f"HexPatch applied to {patched_count} libmigui.so file(s).")
        return patched_count > 0

    def _add_feas_property(self):
        """Add persist.sys.feas.enable=true to mi_ext/etc/build.prop."""
        prop_file = self.ctx.target_dir / "mi_ext" / "etc" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)

        content = ""
        lines = []
        if prop_file.exists():
            content = prop_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()

        if "persist.sys.feas.enable=true" in content:
            self.logger.info("persist.sys.feas.enable=true already exists.")
            return

        lines.append("persist.sys.feas.enable=true")
        prop_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.logger.info("Added persist.sys.feas.enable=true to mi_ext/build.prop")
