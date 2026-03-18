import concurrent.futures
import hashlib
import logging
import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.contextpatch import ContextPatcher
from src.utils.fspatch import patch_fs_config
from src.utils.shell import ShellRunner


class Repacker:
    def __init__(self, context: Any):
        """
        :param context: PortingContext object containing target_dir and other info
        """
        self.ctx = context
        self.logger: logging.Logger = logging.getLogger("Packer")
        self.shell: ShellRunner = ShellRunner()

        self.bin_dir: Path = Path("bin").resolve()
        self.selinux_patcher: ContextPatcher = ContextPatcher()
        self.fix_timestamp: str = "1230768000"
        self.out_dir: Path = Path("out").resolve()
        self.product_out: Path = self.out_dir / "target" / "product" / self.ctx.stock_rom_code
        self.images_out: Path = self.product_out / "IMAGES"
        self.meta_out: Path = self.product_out / "META"
        self.ota_tools_dir: Path = Path("otatools").resolve()

    def pack_all(self, pack_type: str = "EROFS", is_rw: bool = False) -> None:
        """
        Pack all partitions under target directory (parallel optimization)
        :param pack_type: "EXT" (ext4) or "EROFS"
        :param is_rw: Read-write mode (only valid for EXT4)
        """
        self.logger.info(f"Starting repack with format: {pack_type}")

        partitions: List[str] = []
        for item in self.ctx.target_dir.iterdir():
            if item.is_dir() and item.name not in ["config", "repack_images"]:
                partitions.append(item.name)

        max_workers: int = 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for part_name in partitions:
                futures.append(executor.submit(self._pack_partition, part_name, pack_type, is_rw))

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except (concurrent.futures.CancelledError, concurrent.futures.TimeoutError) as e:
                    self.logger.error(f"Partition packing failed: {e}")
                    raise
                except RuntimeError as e:
                    self.logger.error(f"Partition packing failed: {e}")
                    raise

    def _pack_partition(self, part_name: str, pack_type: str, is_rw: bool) -> None:
        src_dir: Path = self.ctx.target_dir / part_name
        img_output: Path = self.ctx.target_dir / f"{part_name}.img"
        fs_config: Path = self.ctx.target_config_dir / f"{part_name}_fs_config"
        file_contexts: Path = self.ctx.target_config_dir / f"{part_name}_file_contexts"

        self.logger.info(f"Packing [{part_name}] as {pack_type}...")
        self._run_patch_tools(src_dir, fs_config, file_contexts)

        if pack_type == "EXT":
            self._pack_ext4(part_name, src_dir, img_output, fs_config, file_contexts, is_rw)
        else:
            self._pack_erofs(part_name, src_dir, img_output, fs_config, file_contexts)

    def _run_patch_tools(self, src_dir: Path, fs_config: Path, file_contexts: Path) -> None:
        """Call patching tools from utils"""
        if fs_config.exists():
            try:
                patch_fs_config(src_dir, fs_config)
            except OSError as e:
                self.logger.error(f"Error patching fs_config: {e}")
        else:
            self.logger.warning(f"fs_config not found for {src_dir.name}, skipping fspatch.")

        if file_contexts.exists():
            try:
                self.selinux_patcher.patch(src_dir, file_contexts)
            except OSError as e:
                self.logger.error(f"Error patching file_contexts: {e}")
        else:
            self.logger.warning(
                f"file_contexts not found for {src_dir.name}, skipping contextpatch."
            )

    def _pack_erofs(
        self, part_name: str, src_dir: Path, img_output: Path, fs_config: Path, file_contexts: Path
    ) -> None:
        """Pack EROFS image"""
        cmd: List[str] = [
            "mkfs.erofs",
            "-zlz4hc,9",
            "-T",
            self.fix_timestamp,
            "--mount-point",
            f"/{part_name}",
            "--fs-config-file",
            str(fs_config),
            "--file-contexts",
            str(file_contexts),
            str(img_output),
            str(src_dir),
        ]
        try:
            self.shell.run(cmd)
            self.logger.info(f"Successfully packed {part_name}.img (EROFS)")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to pack {part_name}: {e}")

    def _pack_ext4(
        self,
        part_name: str,
        src_dir: Path,
        img_output: Path,
        fs_config: Path,
        file_contexts: Path,
        is_rw: bool,
    ) -> None:
        """Pack EXT4 image with size calculation and regeneration"""
        size_orig: int = self._get_dir_size(src_dir)

        if size_orig < 1048576:  # 1MB
            size: int = 1048576
        elif size_orig < 104857600:  # 100MB
            size = int(size_orig * 1.15)
        elif size_orig < 1073741824:  # 1GB
            size = int(size_orig * 1.08)
        else:
            size = int(size_orig * 1.03)

        size = (size // 4096) * 4096

        lost_found: Path = src_dir / "lost+found"
        lost_found.mkdir(exist_ok=True)

        inode_count: int = 5000
        try:
            with open(fs_config, "r") as f:
                inode_count = sum(1 for _ in f) + 8
        except OSError:
            pass

        self._make_ext4_image(
            part_name, src_dir, img_output, size, inode_count, fs_config, file_contexts, is_rw
        )
        self.shell.run(["resize2fs", "-f", "-M", str(img_output)])

        if part_name == "mi_ext":
            return

        free_blocks: int = self._get_free_blocks(img_output)
        if free_blocks > 0:
            free_size: int = free_blocks * 4096
            current_img_size: int = img_output.stat().st_size
            new_size: int = (current_img_size - free_size) // 4096 * 4096

            self.logger.info(f"Regenerating {part_name}.img with optimized size: {new_size}")
            img_output.unlink()
            self._make_ext4_image(
                part_name,
                src_dir,
                img_output,
                new_size,
                inode_count,
                fs_config,
                file_contexts,
                is_rw,
            )
            self.shell.run(["resize2fs", "-f", "-M", str(img_output)])

    def _make_ext4_image(
        self,
        part_name: str,
        src_dir: Path,
        img_path: Path,
        size: int,
        inodes: int,
        fs_config: Path,
        file_contexts: Path,
        is_rw: bool,
    ) -> None:
        """Execute mke2fs and e2fsdroid"""
        mkfs_cmd: List[str] = [
            "mke2fs",
            "-O",
            "^has_journal",
            "-L",
            part_name,
            "-I",
            "256",
            "-N",
            str(inodes),
            "-M",
            f"/{part_name}",
            "-m",
            "0",
            "-t",
            "ext4",
            "-b",
            "4096",
            str(img_path),
            str(size // 4096),
        ]
        self.shell.run(mkfs_cmd)

        e2fs_cmd: List[str] = [
            "e2fsdroid",
            "-e",
            "-T",
            self.fix_timestamp,
            "-C",
            str(fs_config),
            "-S",
            str(file_contexts),
            "-f",
            str(src_dir),
            "-a",
            f"/{part_name}",
            str(img_path),
        ]
        if not is_rw:
            e2fs_cmd.insert(-1, "-s")
        self.shell.run(e2fs_cmd)

    def _get_dir_size(self, path: Path) -> int:
        """Calculate directory size using du -sb"""
        try:
            output: str = subprocess.check_output(["du", "-sb", str(path)], text=True)
            return int(output.split()[0])
        except (subprocess.SubprocessError, ValueError, FileNotFoundError) as e:
            self.logger.warning(f"du command failed, falling back to python: {e}")
            total: int = 0
            for p in path.rglob("*"):
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            return total if total > 0 else 4096

    def _get_free_blocks(self, img_path: Path) -> int:
        """Parse tune2fs -l output to get Free blocks"""
        try:
            output: str = subprocess.check_output(["tune2fs", "-l", str(img_path)], text=True)
            for line in output.splitlines():
                if "Free blocks:" in line:
                    return int(line.split(":")[1].strip())
        except (subprocess.SubprocessError, ValueError, FileNotFoundError):
            return 0
        return 0

    def pack_super_image(self) -> None:
        """Pack super.img for non-payload.bin ROMs"""
        self.logger.info("Packing super.img...")

        lpmake_path: Path = self.ota_tools_dir / "bin" / "lpmake"
        if not lpmake_path.exists():
            self.logger.error(f"lpmake not found at {lpmake_path}")
            return

        super_img: Path = self.ctx.target_dir / "super.img"
        super_size: int = self._get_super_size()

        base_args: List[str] = [
            str(lpmake_path),
            "--metadata-size",
            "65536",
            "--super-name",
            "super",
            "--block-size",
            "4096",
            "--device",
            f"super:{super_size}",
            "--output",
            str(super_img),
        ]

        if not self.ctx.is_ab_device:
            self.logger.info("Packing A-only super.img")
            base_args.extend(
                ["--metadata-slots", "2", "--group", f"qti_dynamic_partitions:{super_size}", "-F"]
            )
            partitions: List[str] = [
                "odm",
                "mi_ext",
                "system",
                "system_ext",
                "product",
                "vendor",
                "odm_dlkm",
                "vendor_dlkm",
                "system_dlkm",
                "product_dlkm",
            ]
            for part in partitions:
                img_path: Path = self.ctx.target_dir / f"{part}.img"
                if img_path.exists():
                    size: int = img_path.stat().st_size
                    base_args.extend(
                        [
                            "--partition",
                            f"{part}:none:{size}:qti_dynamic_partitions",
                            "--image",
                            f"{part}={img_path}",
                        ]
                    )
        else:
            self.logger.info("Packing V-AB super.img")
            base_args.extend(
                [
                    "--virtual-ab",
                    "--metadata-slots",
                    "3",
                    "--group",
                    f"qti_dynamic_partitions_a:{super_size}",
                    "--group",
                    f"qti_dynamic_partitions_b:{super_size}",
                    "-F",
                ]
            )
            partitions = [
                "odm",
                "mi_ext",
                "system",
                "system_ext",
                "product",
                "vendor",
                "odm_dlkm",
                "vendor_dlkm",
                "system_dlkm",
                "product_dlkm",
            ]
            for part in partitions:
                img_path = self.ctx.target_dir / f"{part}.img"
                if img_path.exists():
                    size = img_path.stat().st_size
                    base_args.extend(
                        [
                            "--partition",
                            f"{part}_a:none:{size}:qti_dynamic_partitions_a",
                            "--image",
                            f"{part}_a={img_path}",
                            "--partition",
                            f"{part}_b:none:0:qti_dynamic_partitions_b",
                        ]
                    )

        try:
            self.shell.run(base_args)
            self.logger.info("super.img generated successfully.")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to generate super.img: {e}")
            return

        self.logger.info("Compressing super.img to super.zst...")
        zst_path: Path = self.ctx.target_dir / "super.zst"
        try:
            self.shell.run(["zstd", "--rm", str(super_img), "-o", str(zst_path)])
            self.logger.info("Compressed super.zst generated.")
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"zstd compression failed: {e}.")

        self._generate_flash_script(zst_path if zst_path.exists() else super_img)

    def _generate_flash_script(self, super_image_path: Path) -> None:
        """Generate hybrid flashing scripts (Fastboot + Recovery)"""
        self.logger.info("Generating hybrid flashing scripts...")
        out_name: str = f"{self.ctx.stock_rom_code}_{self.ctx.target_rom_version}_hybrid"
        out_path: Path = self.out_dir / out_name

        if out_path.exists():
            shutil.rmtree(out_path)
        out_path.mkdir(parents=True, exist_ok=True)

        bin_windows: Path = out_path / "bin/windows"
        bin_windows.mkdir(parents=True, exist_ok=True)
        firmware_update: Path = out_path / "firmware-update"
        firmware_update.mkdir(parents=True, exist_ok=True)
        meta_inf: Path = out_path / "META-INF/com/google/android"
        meta_inf.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"Copying {super_image_path.name}...")
        shutil.copy2(super_image_path, out_path / "super.zst")

        if self.ctx.repack_images_dir.exists():
            for fw in self.ctx.repack_images_dir.glob("*.img"):
                if fw.name == "boot.img":
                    shutil.copy2(fw, out_path / "boot.img")
                else:
                    shutil.copy2(fw, firmware_update)

        flash_template: Path = Path("bin/flash")
        if flash_template.exists():
            if (flash_template / "platform-tools-windows").exists():
                shutil.copytree(
                    flash_template / "platform-tools-windows", bin_windows, dirs_exist_ok=True
                )
            zstd_bin: Path = flash_template / "zstd"
            if zstd_bin.exists():
                shutil.copy2(zstd_bin, out_path / "META-INF/zstd")

            files_to_process: Dict[str, Path] = {
                "windows_flash_script.bat": out_path / "windows_flash_script.bat",
                "mac_linux_flash_script.sh": out_path / "mac_linux_flash_script.sh",
                "update-binary": meta_inf / "update-binary",
            }
            (meta_inf / "updater-script").write_text("# dummy\n", encoding="utf-8")

            for src_name, dest_path in files_to_process.items():
                src_file = flash_template / src_name
                if src_file.exists():
                    shutil.copy2(src_file, dest_path)
                    self._process_script_placeholders(dest_path)
                    if "flash_script" in src_name:
                        if not self.ctx.is_ab_device:
                            self._patch_script_for_a_only(dest_path)
                        self._patch_script_for_firmware(dest_path, firmware_update)
                    if src_name == "update-binary":
                        if not self.ctx.is_ab_device:
                            self._patch_update_binary_for_a_only(dest_path)
                        self._patch_update_binary_firmware(dest_path, firmware_update)

        self.logger.info("Zipping hybrid package...")
        timestamp: str = datetime.now().strftime("%Y%m%d%H%M%S")
        final_zip_name: str = (
            f"{self.ctx.stock_rom_code}-hybrid-{self.ctx.target_rom_version}-{timestamp}.zip"
        )
        final_zip_path: Path = self.out_dir / final_zip_name

        with zipfile.ZipFile(final_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(out_path):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(out_path)
                    if file == "super.zst":
                        zf.write(file_path, arcname, compress_type=zipfile.ZIP_STORED)
                    else:
                        zf.write(file_path, arcname)

        md5: str = hashlib.md5(open(final_zip_path, "rb").read()).hexdigest()[:10]
        prefix = "xiaomi.eu_" if getattr(self.ctx, "is_port_eu_rom", False) else ""
        renamed_zip_name: str = f"{prefix}{self.ctx.stock_rom_code}_Hybrid_{self.ctx.target_rom_version}_{self.ctx.security_patch}_{md5}_{timestamp}.zip"
        renamed_zip_path: Path = self.out_dir / renamed_zip_name
        final_zip_path.rename(renamed_zip_path)
        self.logger.info(f"Hybrid ROM generated: {renamed_zip_path}")
        shutil.rmtree(out_path)

    def _process_script_placeholders(self, file_path: Path) -> None:
        """Replace placeholders in scripts/update-binary"""
        content: str = file_path.read_text(encoding="utf-8", errors="ignore")
        replacements: Dict[str, Any] = {
            "device_code": self.ctx.stock_rom_code,
            "baseversion": self.ctx.base_android_version,
            "portversion": self.ctx.target_rom_version,
        }
        for key, value in replacements.items():
            content = content.replace(key, str(value))
        file_path.write_text(content, encoding="utf-8")

    def _patch_script_for_a_only(self, script_path: Path) -> None:
        """Remove _a/_b references for A-only devices (Fastboot)"""
        content: str = script_path.read_text(encoding="utf-8", errors="ignore")
        content = content.replace("_a", "").replace("_b", "")
        new_lines: List[str] = [line for line in content.splitlines() if "_b" not in line]
        script_path.write_text("\n".join(new_lines), encoding="utf-8")

    def _patch_update_binary_for_a_only(self, script_path: Path) -> None:
        """Patch update-binary for A-only devices (Recovery)"""
        content: str = script_path.read_text(encoding="utf-8", errors="ignore")
        content = (
            content.replace("boot_a", "boot")
            .replace("boot_b", "boot")
            .replace("dtbo_a", "dtbo")
            .replace("dtbo_b", "dtbo")
        )
        content = content.replace("bootctl set-active-boot-slot a", "")
        new_lines: List[str] = [
            line for line in content.splitlines() if "lptools unmap" not in line
        ]
        script_path.write_text("\n".join(new_lines), encoding="utf-8")

    def _patch_update_binary_firmware(self, script_path: Path, firmware_dir: Path) -> None:
        """Inject firmware flashing commands into update-binary"""
        fw_files: List[str] = [f.name for f in firmware_dir.glob("*")]
        if not fw_files:
            return

        content: str = script_path.read_text(encoding="utf-8", errors="ignore")
        insertion: List[str] = []
        for fw in fw_files:
            part: str = fw.split(".")[0]
            if fw == "uefi_sec.mbn":
                part = "uefisecapp"
            elif fw == "qupv3fw.elf":
                part = "qupfw"
            elif fw == "NON-HLOS.bin":
                part = "modem"
            elif fw == "km4.mbn":
                part = "keymaster"
            elif fw == "BTFM.bin":
                part = "bluetooth"
            elif fw == "dspso.bin":
                part = "dsp"

            if "dtbo" in fw or "cust" in fw or fw == "boot.img":
                continue

            if self.ctx.is_ab_device:
                insertion.append(
                    f'package_extract_file "firmware-update/{fw}" "/dev/block/bootdevice/by-name/{part}_a"'
                )
                insertion.append(
                    f'package_extract_file "firmware-update/{fw}" "/dev/block/bootdevice/by-name/{part}_b"'
                )
            else:
                insertion.append(
                    f'package_extract_file "firmware-update/{fw}" "/dev/block/bootdevice/by-name/{part}"'
                )

        marker: str = "# firmware"
        if marker in content:
            parts: List[str] = content.split(marker)
            new_content: str = parts[0] + marker + "\n" + "\n".join(insertion) + parts[1]
            script_path.write_text(new_content, encoding="utf-8")
        else:
            self.logger.warning(f"Marker '{marker}' not found in update-binary.")

    def _patch_script_for_firmware(self, script_path: Path, firmware_dir: Path) -> None:
        """Inject firmware flash commands"""
        fw_files: List[str] = [f.name for f in firmware_dir.glob("*")]
        if not fw_files:
            return

        content: str = script_path.read_text(encoding="utf-8", errors="ignore")
        is_windows: bool = script_path.suffix == ".bat"
        insertion: List[str] = []

        for fw in fw_files:
            part: str = fw.split(".")[0]
            if fw == "uefi_sec.mbn":
                part = "uefisecapp"
            elif fw == "qupv3fw.elf":
                part = "qupfw"
            elif fw == "NON-HLOS.bin":
                part = "modem"
            elif fw == "km4.mbn":
                part = "keymaster"
            elif fw == "BTFM.bin":
                part = "bluetooth"
            elif fw == "dspso.bin":
                part = "dsp"
            if "dtbo" in fw or "cust" in fw or fw == "boot.img":
                continue

            if self.ctx.is_ab_device:
                if is_windows:
                    insertion.append(
                        f"bin\\windows\\fastboot.exe flash {part}_a %~dp0firmware-update\\{fw}"
                    )
                    insertion.append(
                        f"bin\\windows\\fastboot.exe flash {part}_b %~dp0firmware-update\\{fw}"
                    )
                else:
                    insertion.append(f"fastboot flash {part}_a firmware-update/{fw}")
                    insertion.append(f"fastboot flash {part}_b firmware-update/{fw}")
            else:
                if is_windows:
                    insertion.append(
                        f"bin\\windows\\fastboot.exe flash {part} %~dp0firmware-update\\{fw}"
                    )
                else:
                    insertion.append(f"fastboot flash {part} firmware-update/{fw}")

        marker: str = "REM firmware" if is_windows else "# firmware"
        if marker in content:
            parts = content.split(marker)
            new_content = parts[0] + marker + "\n" + "\n".join(insertion) + parts[1]
            script_path.write_text(new_content, encoding="utf-8")

    def _get_partition_list(self) -> List[str]:
        """Get list of logical partitions to pack.

        Priority:
        1. Device config pack.partitions
        2. partition_info.json (auto-generated)
        3. Default list
        """
        # Check device config first
        config_partitions = self.ctx.device_config.get("pack", {}).get("partitions")
        if config_partitions:
            self.logger.info(f"Using partitions from device config: {config_partitions}")
            return config_partitions

        # Check for auto-generated partition_info.json
        partition_info_path = Path(f"devices/{self.ctx.stock_rom_code}/partition_info.json")
        if partition_info_path.exists():
            try:
                import json

                with open(partition_info_path, "r") as f:
                    info = json.load(f)
                partitions = info.get("dynamic_partitions", [])
                if partitions:
                    self.logger.info(f"Using partitions from partition_info.json: {partitions}")
                    return partitions
            except Exception as e:
                self.logger.warning(f"Failed to read partition_info.json: {e}")

        # Fall back to default list
        default_partitions = [
            "system",
            "system_ext",
            "product",
            "vendor",
            "odm",
            "mi_ext",
            "system_dlkm",
            "vendor_dlkm",
        ]
        self.logger.info(f"Using default partition list: {default_partitions}")
        return default_partitions

    def _get_super_size(self) -> int:
        """Get Super partition size."""
        # 1. Check from device config first
        if hasattr(self.ctx, "device_config"):
            super_size = self.ctx.device_config.get("pack", {}).get("super_size")
            if super_size:
                return int(super_size)

        # 2. Check from partition_info.json
        partition_info_path = Path(f"devices/{self.ctx.stock_rom_code}/partition_info.json")
        if partition_info_path.exists():
            try:
                import json

                with open(partition_info_path, "r") as f:
                    info = json.load(f)
                super_size = info.get("super_size")
                if super_size:
                    self.logger.info(f"Using super_size from partition_info.json: {super_size}")
                    return int(super_size)
            except Exception as e:
                self.logger.debug(f"Failed to read super_size from partition_info.json: {e}")

        # 3. Fallback to hardcoded map
        device_code: str = self.ctx.stock_rom_code.upper()
        size_map: Dict[int, List[str]] = {
            9663676416: ["FUXI", "NUWA", "ISHTAR", "MARBLE", "SOCRATES", "BABYLON"],
            9122611200: ["SUNSTONE"],
            11811160064: ["YUDI"],
            13411287040: ["PANDORA", "POPSICLE", "PUDDING", "NEZHA"],
        }
        for size, devices in size_map.items():
            if device_code in devices:
                return size
        return 9126805504

    def pack_ota_payload(self) -> None:
        """Pack AOSP OTA payload"""
        self.logger.info("Starting OTA Payload packing...")
        if self.product_out.exists():
            shutil.rmtree(self.product_out)
        self.images_out.mkdir(parents=True, exist_ok=True)
        self.meta_out.mkdir(parents=True, exist_ok=True)

        # Get partition list from config or auto-detect
        pack_partitions = self._get_partition_list()

        # Create directories for all partitions
        for part in pack_partitions:
            (self.product_out / part.upper()).mkdir(exist_ok=True)

        # Copy all partition images
        for img in self.ctx.target_dir.glob("*.img"):
            shutil.copy2(img, self.images_out)
        if self.ctx.repack_images_dir.exists():
            for img in self.ctx.repack_images_dir.glob("*.img"):
                shutil.copy2(img, self.images_out)

        device_custom_dir: Path = Path(f"devices/{self.ctx.stock_rom_code}")
        if device_custom_dir.exists():
            ksu_boot: List[Path] = list(device_custom_dir.glob("boot*.img"))
            if ksu_boot:
                shutil.copy2(ksu_boot[0], self.images_out / "boot.img")
                self.logger.info(f"Replaced boot.img with {ksu_boot[0].name}")
            dtbo: List[Path] = list(device_custom_dir.glob("dtbo*.img"))
            if dtbo:
                shutil.copy2(dtbo[0], self.images_out / "dtbo.img")
            rec: Path = device_custom_dir / "recovery.img"
            if rec.exists():
                shutil.copy2(rec, self.images_out)
            init_boot: Path = device_custom_dir / "init_boot-kernelsu.img"
            if init_boot.exists():
                shutil.copy2(init_boot, self.images_out / "init_boot.img")

        self._generate_meta_info()
        self._copy_build_props()
        self._run_ota_tool()

    def _generate_meta_info(self) -> None:
        """Generate ab_partitions.txt, dynamic_partitions_info.txt, misc_info.txt"""
        self.logger.info("Generating META info...")
        partition_list: List[str] = [
            img.stem for img in self.images_out.glob("*.img") if img.stem != "cust"
        ]
        with open(self.meta_out / "ab_partitions.txt", "w") as f:
            for p in sorted(partition_list):
                f.write(f"{p}\n")

        super_size: int = self._get_super_size()
        group_size: int = super_size - 1048576
        super_parts: List[str] = [
            p
            for p in partition_list
            if p
            in [
                "system",
                "vendor",
                "product",
                "system_ext",
                "odm",
                "mi_ext",
                "odm_dlkm",
                "vendor_dlkm",
                "system_dlkm",
                "product_dlkm",
            ]
        ]
        with open(self.meta_out / "dynamic_partitions_info.txt", "w") as f:
            f.write(
                f"super_partition_size={super_size}\nsuper_partition_groups=qti_dynamic_partitions\nsuper_qti_dynamic_partitions_group_size={group_size}\nsuper_qti_dynamic_partitions_partition_list={' '.join(super_parts)}\nvirtual_ab=true\nvirtual_ab_compression=true\n"
            )

        with open(self.meta_out / "misc_info.txt", "w") as f:
            f.write("recovery_api_version=3\nfstab_version=2\nab_update=true\n")
        with open(self.meta_out / "update_engine_config.txt", "w") as f:
            f.write("PAYLOAD_MAJOR_VERSION=2\nPAYLOAD_MINOR_VERSION=8\n")

    def _copy_build_props(self) -> None:
        """Copy build.prop of each partition to directories required by META structure"""
        mapping: Dict[str, str] = {
            "system": "SYSTEM",
            "product": "PRODUCT",
            "system_ext": "SYSTEM_EXT",
            "vendor": "VENDOR",
            "odm": "ODM",
        }
        for part_lower, part_upper in mapping.items():
            src_prop: Optional[Path] = self.ctx.get_target_prop_file(part_lower)
            if src_prop and src_prop.exists():
                shutil.copy2(src_prop, self.product_out / part_upper / "build.prop")
            else:
                self.logger.warning(f"build.prop for {part_lower} not found.")

    def _run_ota_tool(self) -> None:
        """Call ota_from_target_files to generate ZIP"""
        self.logger.info("Running ota_from_target_files...")
        timestamp: str = datetime.now().strftime("%Y%m%d%H%M%S")
        output_zip: Path = self.out_dir / f"{self.ctx.stock_rom_code}-ota_full-{timestamp}.zip"
        key_path: Path = self.ota_tools_dir / "security" / "testkey"

        custom_tmp_dir: Path = self.out_dir / "tmp"
        if custom_tmp_dir.exists():
            shutil.rmtree(custom_tmp_dir)
        custom_tmp_dir.mkdir(parents=True, exist_ok=True)

        env: Dict[str, str] = os.environ.copy()
        env["PATH"] = f"{self.ota_tools_dir}/bin:{env['PATH']}"
        env["TMPDIR"] = str(custom_tmp_dir)

        try:
            self.shell.run(
                [
                    str(self.ota_tools_dir / "bin" / "ota_from_target_files"),
                    "-v",
                    "-k",
                    str(key_path),
                    str(self.product_out),
                    str(output_zip),
                ],
                env=env,
            )
            md5: str = hashlib.md5(open(output_zip, "rb").read()).hexdigest()[:10]
            prefix = "xiaomi.eu_" if getattr(self.ctx, "is_port_eu_rom", False) else ""
            final_path: Path = (
                self.out_dir
                / f"{prefix}{self.ctx.stock_rom_code}-ota_full-{self.ctx.target_rom_version}-{self.ctx.security_patch}-{timestamp}-{md5}-{self.ctx.port_android_version}.zip"
            )
            output_zip.rename(final_path)
            self.logger.info(f"Final OTA Package: {final_path}")
        except (subprocess.CalledProcessError, OSError) as e:
            self.logger.error(f"OTA generation failed: {e}")
