"""Firmware-level modifications (vbmeta patching, KernelSU)."""

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional
import urllib.request

from src.utils.shell import ShellRunner
from src.core.modifiers.base_modifier import BaseModifier


class FirmwareModifier(BaseModifier):
    """Handles firmware-level modifications."""

    def __init__(self, context):
        super().__init__(context, "FirmwareModifier")
        self.shell = ShellRunner()
        self.bin_dir = Path("bin").resolve()

        if not self.ctx.tools.magiskboot.exists():
            self.logger.error(
                f"magiskboot binary not found at {self.ctx.tools.magiskboot}"
            )
            return

        self.assets_dir = self.bin_dir.parent / "assets"
        self.ksu_version_file = self.assets_dir / "ksu_version.txt"

        # Default values that can be overridden via context configuration
        default_repo_owner = "tiann"  # Former default - kept for compatibility only
        default_repo_name = "KernelSU"  # Former default - kept for compatibility only

        # Allow configuration overrides from device_config or context
        self.repo_owner = getattr(self.ctx, "ksu_repo_owner", default_repo_owner)
        self.repo_name = getattr(self.ctx, "ksu_repo_name", default_repo_name)

        # Make these values configurable via device config
        if hasattr(self.ctx, "device_config") and self.ctx.device_config:
            self.repo_owner = self.ctx.device_config.get(
                "ksu_repo_owner", self.repo_owner
            )
            self.repo_name = self.ctx.device_config.get("ksu_repo_name", self.repo_name)
            self.ksu_config_url_template = self.ctx.device_config.get(
                "ksu_gh_api_url_template",
                f"https://api.github.com/repos/{{owner}}/{{repo}}/releases/latest",
            )
        else:
            # Use config directly from ctx if available
            self.ksu_config_url_template = getattr(
                self.ctx,
                "ksu_gh_api_url_template",
                f"https://api.github.com/repos/{{owner}}/{{repo}}/releases/latest",
            )

    def run(self):
        """Execute all firmware modifications."""
        self.logger.info("Starting Firmware Modification...")

        kmi_version = self._get_kmi_version()
        if kmi_version:
            self.logger.info(f"Detected KMI Version: {kmi_version}")
            if kmi_version == "android16-6.12":
                self.logger.info(
                    "KMI android16-6.12 detected. Skipping vbmeta patching and modifying vendor_boot fstab instead."
                )
                self._patch_vendor_boot_fstab()
            else:
                self._patch_vbmeta()
        else:
            self._patch_vbmeta()

        if getattr(self.ctx, "enable_ksu", False):
            self._patch_ksu(kmi_version)

        self.logger.info("Firmware Modification Completed.")

    def _get_kmi_version(self) -> Optional[str]:
        """Get KMI version from boot or init_boot image."""
        target_init_boot = self.ctx.target_dir / "repack_images" / "init_boot.img"
        target_boot = self.ctx.target_dir / "repack_images" / "boot.img"

        # Prefer boot.img for KMI analysis if it exists, otherwise use init_boot.img
        analysis_target = (
            target_boot
            if target_boot.exists()
            else (target_init_boot if target_init_boot.exists() else None)
        )

        if not analysis_target:
            return None

        return self._analyze_kmi(analysis_target)

    def _patch_vendor_boot_fstab(self):
        """Modify fstab in vendor_boot to disable AVB for KMI 6.12."""
        self.logger.info("Patching vendor_boot fstab...")
        vendor_boot = self.ctx.repack_images_dir / "vendor_boot.img"
        if not vendor_boot.exists():
            self.logger.warning("vendor_boot.img not found, skipping fstab patch.")
            return

        with tempfile.TemporaryDirectory(prefix="vendor_boot_patch_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(vendor_boot, tmp_path / "vendor_boot.img")

            try:
                self.shell.run(
                    [str(self.ctx.tools.magiskboot), "unpack", "vendor_boot.img"],
                    cwd=tmp_path,
                )
            except Exception as e:
                self.logger.error(f"Failed to unpack vendor_boot.img: {e}")
                return

            ramdisk = tmp_path / "ramdisk.cpio"
            if not ramdisk.exists():
                self.logger.error("ramdisk.cpio not found in vendor_boot")
                return

            # Attempt to decompress the ramdisk explicitly, as 'raw' format in v4 images
            # might still be compressed in a way magiskboot cpio doesn't auto-detect.
            try:
                self.shell.run(
                    [
                        str(self.ctx.tools.magiskboot),
                        "decompress",
                        "ramdisk.cpio",
                        "ramdisk.cpio.dec",
                    ],
                    cwd=tmp_path,
                )
                if (tmp_path / "ramdisk.cpio.dec").exists():
                    shutil.move(tmp_path / "ramdisk.cpio.dec", ramdisk)
                    self.logger.info("Successfully decompressed vendor_boot ramdisk.")
            except Exception:
                # If decompression fails, it might actually be raw, so we continue
                pass

            # Extract all files to find fstab using system cpio if magiskboot fails
            extract_dir = tmp_path / "extracted_ramdisk"
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                # Try system cpio first as it is often more robust for extraction
                self.shell.run(
                    f"cpio -id < {ramdisk}",
                    cwd=extract_dir,
                    shell=True
                )
            except Exception as e:
                self.logger.warning(f"System cpio failed, trying magiskboot cpio: {e}")
                try:
                    self.shell.run(
                        [str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "extract"],
                        cwd=extract_dir,
                    )
                except Exception as e2:
                    self.logger.error(f"Both cpio methods failed: {e2}")
                    return

            fstab_files = list(extract_dir.rglob("fstab.*"))
            if not fstab_files:
                self.logger.info("No fstab files found in vendor_boot ramdisk.")

            patched = False
            for fstab_path in fstab_files:
                # Use relative path for entry name in cpio
                fstab_entry_name = str(fstab_path.relative_to(extract_dir))
                self.logger.info(f"Checking {fstab_entry_name} in vendor_boot ramdisk...")
                
                with open(fstab_path, "r") as f:
                    content = f.read()

                new_content = self._disable_avb_verify(content)

                if new_content != content:
                    with open(fstab_path, "w") as f:
                        f.write(new_content)

                    # Add back to ramdisk
                    # magiskboot cpio <cpio> 'add MODE ENTRY INFILE'
                    self.shell.run(
                        [
                            str(self.ctx.tools.magiskboot),
                            "cpio",
                            "ramdisk.cpio",
                            f"add 0644 {fstab_entry_name} {fstab_path}",
                        ],
                        cwd=tmp_path,
                    )
                    patched = True
                    self.logger.info(f"Successfully patched {fstab_entry_name} in vendor_boot.")

            # Also patch DTB if it exists
            dtb_file = tmp_path / "dtb"
            if dtb_file.exists():
                self.logger.info("Found DTB in vendor_boot, attempting optional fstab patch...")
                # Note: magiskboot dtb patch might fail if it doesn't find fstab nodes,
                # we set check=False to avoid error logs in ShellRunner.
                try:
                    res = self.shell.run(
                        [str(self.ctx.tools.magiskboot), "dtb", "dtb", "patch"],
                        cwd=tmp_path,
                        check=False,
                        capture_output=True
                    )
                    if res.returncode == 0:
                        patched = True
                        self.logger.info("DTB fstab nodes patched successfully.")
                    else:
                        self.logger.info("No patchable fstab nodes found in DTB, skipping.")
                except Exception as e:
                    self.logger.debug(f"Optional DTB patch failed: {e}")

            if patched:
                self.logger.info("Repacking vendor_boot.img...")
                # magiskboot repack will automatically compress ramdisk.cpio 
                # based on the format detected in the original vendor_boot.img
                try:
                    self.shell.run(
                        [str(self.ctx.tools.magiskboot), "repack", "vendor_boot.img"],
                        cwd=tmp_path,
                    )
                    new_img = tmp_path / "new-boot.img"
                    if new_img.exists():
                        shutil.move(new_img, vendor_boot)
                        self.logger.info("vendor_boot.img repacked with patched fstab.")
                    else:
                        self.logger.error("Failed to repack vendor_boot.img: new-boot.img not found")
                except Exception as e:
                    self.logger.error(f"Failed to repack vendor_boot.img: {e}")

    def _disable_avb_verify(self, content: str) -> str:
        """Port of the shell function disable_avb_verify.
        
        Original logic:
        sed -i "s/,avb_keys=.*avbpubkey//g" $fstab
        sed -i "s/,avb=vbmeta_system//g" $fstab
        sed -i "s/,avb=vbmeta_vendor//g" $fstab
        sed -i "s/,avb=vbmeta//g" $fstab
        sed -i "s/,avb//g" $fstab
        """
        # Remove avb_keys pattern
        content = re.sub(r",avb_keys=[^, \n]*avbpubkey", "", content)
        # Remove specific avb flags
        content = re.sub(r",avb=vbmeta_system", "", content)
        # Handle some variations that might exist
        content = re.sub(r",avb=vbmeta_vendor", "", content)
        content = re.sub(r",avb=vbmeta", "", content)
        content = re.sub(r",avb", "", content)
        
        return content

    def _patch_vbmeta(self):
        """Patch vbmeta images to disable AVB."""
        self.logger.info("Patching vbmeta images (Disabling AVB)...")

        vbmeta_images = list(self.ctx.target_dir.rglob("vbmeta*.img"))

        if not vbmeta_images:
            self.logger.warning("No vbmeta images found in target directory.")
            return

        AVB_MAGIC = b"AVB0"
        FLAGS_OFFSET = 123
        FLAGS_TO_SET = b"\x03"

        for img_path in vbmeta_images:
            try:
                with open(img_path, "r+b") as f:
                    magic = f.read(4)
                    if magic != AVB_MAGIC:
                        self.logger.warning(
                            f"Skipping {img_path.name}: Invalid AVB Magic"
                        )
                        continue

                    f.seek(FLAGS_OFFSET)
                    f.write(FLAGS_TO_SET)
                    self.logger.info(f"Successfully patched: {img_path.name}")

            except Exception as e:
                self.logger.error(f"Failed to patch {img_path.name}: {e}")

    def _patch_ksu(self, kmi_version: Optional[str] = None):
        """Patch KernelSU into boot image."""
        self.logger.info("Attempting to patch KernelSU...")

        target_init_boot = self.ctx.target_dir / "repack_images" / "init_boot.img"
        target_boot = self.ctx.target_dir / "repack_images" / "boot.img"

        patch_target = None
        if target_init_boot.exists():
            patch_target = target_init_boot
        elif target_boot.exists():
            patch_target = target_boot

        if not patch_target:
            self.logger.warning(
                "Neither init_boot.img nor boot.img found, skipping KSU patch."
            )
            return

        if not self.ctx.tools.magiskboot.exists():
            self.logger.error("magiskboot binary not found!")
            return

        if not kmi_version:
            kmi_version = self._analyze_kmi(
                target_boot if target_boot.exists() else patch_target
            )

        if not kmi_version:
            self.logger.error("Failed to determine KMI version.")
            return

        self.logger.info(f"Detected KMI Version: {kmi_version}")

        if not self._prepare_ksu_assets(kmi_version):
            self.logger.error("Failed to prepare KSU assets.")
            return

        self._apply_ksu_patch(patch_target, kmi_version)

    def _analyze_kmi(self, boot_img: Path) -> Optional[str]:
        """Analyze kernel image to extract KMI version."""
        with tempfile.TemporaryDirectory(prefix="ksu_kmi_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(boot_img, tmp_path / "boot.img")

            try:
                self.shell.run(
                    [str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path
                )
            except Exception as e:
                self.logger.debug(f"Magiskboot unpack failed: {e}")
                return None

            kernel_file = tmp_path / "kernel"
            if not kernel_file.exists():
                self.logger.debug("Kernel file not found after unpack.")
                return None

            try:
                with open(kernel_file, "rb") as f:
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

                pattern = re.compile(r"(?:^|\s)(\d+\.\d+)\S*(android\d+)")
                for s in strings:
                    if "Linux version" in s or "android" in s:
                        match = pattern.search(s)
                        if match:
                            return f"{match.group(2)}-{match.group(1)}"
            except Exception as e:
                self.logger.error(f"Error parsing kernel file: {e}")

        self.logger.warning("Could not find KMI version pattern in kernel.")
        return None

    def _prepare_ksu_assets(self, kmi_version):
        """Download KernelSU assets if not present."""
        self.assets_dir.mkdir(parents=True, exist_ok=True)

        # Determine expected file names, configurable via device config
        if hasattr(self.ctx, "device_config") and self.ctx.device_config:
            ko_file_expected = self.ctx.device_config.get(
                "ksu_module_filename", f"{kmi_version}_kernelsu.ko"
            )
            init_file_expected = self.ctx.device_config.get(
                "ksu_init_filename", "ksuinit"
            )
            ko_asset_name_pattern = self.ctx.device_config.get(
                "ksu_module_asset_pattern", f"{kmi_version}_kernelsu.ko"
            )
            init_asset_name = self.ctx.device_config.get(
                "ksu_init_asset_name", "ksuinit"
            )
        else:
            ko_file_expected = f"{kmi_version}_kernelsu.ko"
            init_file_expected = "ksuinit"
            ko_asset_name_pattern = f"{kmi_version}_kernelsu.ko"
            init_asset_name = "ksuinit"

        target_ko = self.assets_dir / ko_file_expected
        target_init = self.assets_dir / init_file_expected

        if target_ko.exists() and target_init.exists():
            return True

        self.logger.info("Downloading KernelSU assets...")
        try:
            api_url = self.ksu_config_url_template.format(
                owner=self.repo_owner, repo=self.repo_name
            )
            with urllib.request.urlopen(api_url, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            assets = data.get("assets", [])

            for asset in assets:
                name = asset["name"]
                url = asset["browser_download_url"]

                if name == init_asset_name and not target_init.exists():
                    self._download_file(url, target_init)
                elif name == ko_asset_name_pattern and not target_ko.exists():
                    self._download_file(url, target_ko)

            return target_ko.exists() and target_init.exists()

        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return False

    def _download_file(self, url, dest):
        """Download a file from URL."""
        self.logger.info(f"Downloading {dest.name}...")
        with urllib.request.urlopen(url) as remote, open(dest, "wb") as local:
            shutil.copyfileobj(remote, local)

    def _apply_ksu_patch(self, target_img, kmi_version):
        """Apply KernelSU patch to boot image."""
        self.logger.info(f"Patching {target_img.name} with KernelSU...")

        # Allow for customization of file paths from config
        if hasattr(self.ctx, "device_config") and self.ctx.device_config:
            ko_filename = self.ctx.device_config.get(
                "ksu_module_filename", f"{kmi_version}_kernelsu.ko"
            )
            init_filename = self.ctx.device_config.get("ksu_init_filename", "ksuinit")
        else:
            ko_filename = f"{kmi_version}_kernelsu.ko"
            init_filename = "ksuinit"

        ko_file = self.assets_dir / ko_filename
        init_file = self.assets_dir / init_filename

        with tempfile.TemporaryDirectory(prefix="ksu_patch_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(target_img, tmp_path / "boot.img")

            self.shell.run(
                [str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path
            )

            ramdisk = tmp_path / "ramdisk.cpio"
            if not ramdisk.exists():
                self.logger.error("ramdisk.cpio not found")
                return

            self.shell.run(
                [
                    str(self.ctx.tools.magiskboot),
                    "cpio",
                    "ramdisk.cpio",
                    "mv init init.real",
                ],
                cwd=tmp_path,
            )

            shutil.copy(init_file, tmp_path / "init")
            self.shell.run(
                [
                    str(self.ctx.tools.magiskboot),
                    "cpio",
                    "ramdisk.cpio",
                    "add 0755 init init",
                ],
                cwd=tmp_path,
            )

            shutil.copy(ko_file, tmp_path / "kernelsu.ko")
            self.shell.run(
                [
                    str(self.ctx.tools.magiskboot),
                    "cpio",
                    "ramdisk.cpio",
                    "add 0755 kernelsu.ko kernelsu.ko",
                ],
                cwd=tmp_path,
            )

            self.shell.run(
                [str(self.ctx.tools.magiskboot), "repack", "boot.img"], cwd=tmp_path
            )

            new_img = tmp_path / "new-boot.img"
            if new_img.exists():
                shutil.move(new_img, target_img)
                self.logger.info(
                    f"KernelSU injected successfully into {target_img.name}."
                )
            else:
                self.logger.error(f"Failed to repack {target_img.name}")
