import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from src.utils.contextpatch import ContextPatcher
from src.utils.fspatch import patch_fs_config
from src.utils.shell import ShellRunner

AVB_DEFAULT_ALGORITHM = "SHA256_RSA4096"
AOSP_AVB_PARTITIONS = {
    "boot",
    "init_boot",
    "dtbo",
    "odm",
    "product",
    "pvmfw",
    "recovery",
    "system",
    "system_ext",
    "vendor",
    "vendor_boot",
    "vendor_kernel_boot",
    "vendor_dlkm",
    "odm_dlkm",
    "system_dlkm",
}


def _append_unique(items: List[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def parse_avbtool_info_output(output: str) -> Dict[str, Any]:
    """Parse `avbtool info_image` output into a structured dictionary."""
    result: Dict[str, Any] = {
        "image_size": None,
        "original_image_size": None,
        "algorithm": None,
        "rollback_index": None,
        "flags": None,
        "chain_partitions": [],
        "hash_partitions": [],
        "hashtree_partitions": [],
    }
    current_desc: Optional[str] = None
    chain_name: Optional[str] = None
    chain_loc: Optional[int] = None
    rollback_re = re.compile(r"^Rollback Index:\s+(\d+)$")
    flags_re = re.compile(r"^Flags:\s+(\d+)$")
    alg_re = re.compile(r"^Algorithm:\s+(.+)$")
    image_size_re = re.compile(r"^Image size:\s+(\d+)\s+bytes$")
    original_image_size_re = re.compile(r"^Original image size:\s+(\d+)\s+bytes$")
    part_re = re.compile(r"^Partition Name:\s+(.+)$")
    chain_loc_re = re.compile(r"^Rollback Index Location:\s+(\d+)$")

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue

        alg_match = alg_re.match(line)
        if alg_match:
            result["algorithm"] = alg_match.group(1).strip()
            continue
        image_size_match = image_size_re.match(line)
        if image_size_match:
            result["image_size"] = int(image_size_match.group(1))
            continue
        original_image_size_match = original_image_size_re.match(line)
        if original_image_size_match:
            result["original_image_size"] = int(original_image_size_match.group(1))
            continue

        rollback_match = rollback_re.match(line)
        if rollback_match:
            result["rollback_index"] = int(rollback_match.group(1))
            continue

        flags_match = flags_re.match(line)
        if flags_match:
            result["flags"] = int(flags_match.group(1))
            continue

        if line == "Chain Partition descriptor:":
            current_desc = "chain"
            chain_name = None
            chain_loc = None
            continue
        if line == "Hash descriptor:":
            current_desc = "hash"
            continue
        if line == "Hashtree descriptor:":
            current_desc = "hashtree"
            continue

        part_match = part_re.match(line)
        if part_match:
            part_name = part_match.group(1).strip()
            if current_desc == "chain":
                chain_name = part_name
                if chain_loc is not None:
                    cast(List[Tuple[str, int]], result["chain_partitions"]).append(
                        (chain_name, chain_loc)
                    )
                    chain_name = None
                    chain_loc = None
            elif current_desc == "hash":
                _append_unique(cast(List[str], result["hash_partitions"]), part_name)
            elif current_desc == "hashtree":
                _append_unique(cast(List[str], result["hashtree_partitions"]), part_name)
            continue

        if current_desc == "chain":
            chain_loc_match = chain_loc_re.match(line)
            if chain_loc_match:
                chain_loc = int(chain_loc_match.group(1))
                if chain_name is not None:
                    cast(List[Tuple[str, int]], result["chain_partitions"]).append(
                        (chain_name, chain_loc)
                    )
                    chain_name = None
                    chain_loc = None

    return result


def build_rom_filename_prefix(ctx: Any) -> str:
    """Build output filename prefix based on ROM type."""
    if getattr(ctx, "is_port_eu_rom", False):
        return "xiaomi.eu_"
    return ""


def build_rom_filename_device_tag(ctx: Any) -> str:
    """Build the device segment used in output filenames."""
    device = str(getattr(ctx, "stock_rom_code", "") or "").strip()
    if not device:
        return "unknown"

    if getattr(ctx, "is_port_eu_rom", False):
        return device

    region = str(getattr(ctx, "port_global_region", "") or "").lower().strip()
    if region and region != "global":
        return f"{device}_{region}_global"

    if getattr(ctx, "is_port_global_rom", False):
        return f"{device}_global"

    return device


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
        self._avb_partition_size: Dict[str, int] = {}
        self._build_prop_cache: Dict[str, Dict[str, str]] = {}

    def _avb_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        ota_bin = str(self.ota_tools_dir / "bin")
        env["PATH"] = f"{ota_bin}:{env.get('PATH', '')}"
        return env

    def _detect_rsa_key_bits(self, key_path: Path) -> Optional[int]:
        try:
            output = subprocess.check_output(
                ["openssl", "pkey", "-in", str(key_path), "-text", "-noout"],
                text=True,
                stderr=subprocess.STDOUT,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return None
        match = re.search(r"Private-Key:\s*\((\d+)\s+bit", output)
        if not match:
            return None
        return int(match.group(1))

    def _algorithm_for_key(self, preferred: str, key_path: Optional[Path]) -> str:
        if not key_path:
            return preferred
        bits = self._detect_rsa_key_bits(key_path)
        if bits is None:
            return preferred
        if bits >= 4096:
            return "SHA256_RSA4096"
        if bits >= 2048:
            return "SHA256_RSA2048"
        return preferred

    def _read_build_prop(self, path: Path) -> Dict[str, str]:
        props: Dict[str, str] = {}
        if not path.exists():
            return props
        try:
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        except OSError:
            return {}
        return props

    def _get_partition_build_props(self, part: str) -> Dict[str, str]:
        if part in self._build_prop_cache:
            return self._build_prop_cache[part]

        path: Optional[Path] = None
        getter = getattr(self.ctx, "get_target_prop_file", None)
        if callable(getter):
            try:
                candidate = getter(part)
                if isinstance(candidate, Path):
                    path = candidate
            except Exception:
                path = None

        if path is None:
            target_dir = getattr(self.ctx, "target_dir", None)
            if isinstance(target_dir, Path):
                part_dir = target_dir / part
                candidates = [
                    part_dir / "build.prop",
                    part_dir / "system" / "build.prop",
                    part_dir / "etc" / "build.prop",
                ]
                for c in candidates:
                    if c.exists():
                        path = c
                        break

        props = self._read_build_prop(path) if path else {}
        self._build_prop_cache[part] = props
        return props

    def _get_prop_value(self, part: str, kind: str) -> Optional[str]:
        props = self._get_partition_build_props(part)
        system_props = self._get_partition_build_props("system")
        keys: List[str]
        if kind == "fingerprint":
            keys = [
                f"ro.{part}.build.fingerprint",
                "ro.build.fingerprint",
            ]
        elif kind == "os_version":
            keys = [
                f"ro.{part}.build.version.release",
                "ro.build.version.release",
                "ro.build.version.release_or_codename",
            ]
        else:  # security_patch
            keys = [
                f"ro.{part}.build.version.security_patch",
                f"ro.{part}.build.security_patch",
                "ro.build.version.security_patch",
            ]

        for key in keys:
            if key in props and props[key]:
                return props[key]
        for key in keys:
            if key in system_props and system_props[key]:
                return system_props[key]
        return None

    def _get_dynamic_partition_metadata(self) -> Optional[Dict[str, Any]]:
        """Load dynamic_partition_metadata from partition_info.json."""
        partition_info_path = self._get_partition_info_path()
        if not partition_info_path.exists():
            return None
        try:
            data = json.loads(partition_info_path.read_text(encoding="utf-8"))
            metadata = data.get("dynamic_partition_metadata")
            return metadata if isinstance(metadata, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _is_virtual_ab_compression_enabled(self) -> bool:
        """Check if Virtual A/B compression is enabled.

        Priority:
        1. dynamic_partition_metadata from partition_info.json
        2. ro.virtual_ab.compression.enabled from vendor build.prop
        """
        metadata = self._get_dynamic_partition_metadata()
        if metadata and metadata.get("vabc_enabled"):
            return True
        vendor_props = self._get_partition_build_props("vendor")
        value = vendor_props.get("ro.virtual_ab.compression.enabled", "").lower()
        return value == "true"

    def _build_footer_props_args(self, part: str, include_hash_algorithm: bool) -> List[str]:
        args: List[str] = []
        if include_hash_algorithm:
            args.extend(["--hash_algorithm", "sha256"])
        prop_prefix = f"com.android.build.{part}"
        os_version = self._get_prop_value(part, "os_version")
        if os_version:
            args.extend(["--prop", f"{prop_prefix}.os_version:{os_version}"])
        fingerprint = self._get_prop_value(part, "fingerprint")
        if fingerprint:
            args.extend(["--prop", f"{prop_prefix}.fingerprint:{fingerprint}"])
        security_patch = self._get_prop_value(part, "security_patch")
        if security_patch:
            args.extend(["--prop", f"{prop_prefix}.security_patch:{security_patch}"])
        return args

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
        prefix = build_rom_filename_prefix(self.ctx)
        device_tag = build_rom_filename_device_tag(self.ctx)
        renamed_zip_name: str = f"{prefix}{device_tag}_Hybrid_{self.ctx.target_rom_version}_{self.ctx.security_patch}_{md5}_{timestamp}.zip"
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
            return cast(List[str], config_partitions)

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
                    return cast(List[str], partitions)
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
                self.logger.info(f"Using super_size from device config: {super_size}")
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
                self.logger.info(f"Using super_size from built-in map for {device_code}: {size}")
                return size
        default_size = 9126805504
        self.logger.info(f"Using default super_size fallback for {device_code}: {default_size}")
        return default_size

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

        # Keep custom partition AVB footer behavior aligned with stock vbmeta.
        current_partitions = [
            img.stem for img in self.images_out.glob("*.img") if img.stem != "cust"
        ]
        if getattr(self.ctx, "enable_custom_avb_chain", False):
            profile = self._collect_stock_avb_profile()
            self._sync_partition_info_from_stock_avb(profile)
            self._apply_avb_to_custom_images(current_partitions)
            self._rebuild_vbmeta_images(current_partitions)

        self._generate_meta_info()
        self._copy_build_props()
        if getattr(self.ctx, "enable_custom_avb_chain", False):
            self._verify_avb_images()
        self._run_ota_tool()

    def _run_avbtool_info_image(self, avbtool: Path, image: Path) -> Optional[Dict[str, Any]]:
        if not image.exists():
            return None
        try:
            output = subprocess.check_output(
                [str(avbtool), "info_image", "--image", str(image)],
                text=True,
                stderr=subprocess.STDOUT,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            self.logger.debug("Failed to inspect %s via avbtool: %s", image.name, e)
            return None
        return parse_avbtool_info_output(output)

    def _get_avb_testkey_path(self) -> Optional[Path]:
        custom_key = getattr(self.ctx, "avb_key_path", None)
        if custom_key and custom_key.exists():
            self.logger.info(f"Using custom AVB key: {custom_key}")
            return custom_key

        candidates = [
            self.ota_tools_dir / "build/make/target/product/security/testkey.pem",
            self.ota_tools_dir / "security/testkey.pem",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        pk8_path = self.ota_tools_dir / "build/make/target/product/security/testkey.pk8"
        pem_path = self.ota_tools_dir / "build/make/target/product/security/testkey.pem"
        if pk8_path.exists() and not pem_path.exists():
            try:
                import subprocess
                subprocess.run(
                    ["openssl", "pkcs8", "-in", str(pk8_path), "-inform", "DER",
                     "-out", str(pem_path), "-nocrypt"],
                    check=True, capture_output=True
                )
                self.logger.info(f"Generated AVB signing key from {pk8_path.name}")
                return pem_path
            except Exception as e:
                self.logger.warning(f"Failed to generate key from {pk8_path}: {e}")

        return None

    def _collect_stock_avb_profile(self) -> Dict[str, Any]:
        """Collect AVB descriptor profile from stock vbmeta images."""
        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        stock_images_dir = Path("build/stockrom/images")
        if not avbtool.exists() or not stock_images_dir.exists():
            return {}

        vbmeta_info = self._run_avbtool_info_image(avbtool, stock_images_dir / "vbmeta.img")
        if not vbmeta_info:
            return {}

        vbmeta_system_info = self._run_avbtool_info_image(
            avbtool, stock_images_dir / "vbmeta_system.img"
        )
        hash_parts = set(cast(List[str], vbmeta_info.get("hash_partitions", [])))
        hashtree_parts = set(cast(List[str], vbmeta_info.get("hashtree_partitions", [])))
        if vbmeta_system_info:
            hashtree_parts.update(
                cast(List[str], vbmeta_system_info.get("hashtree_partitions", []))
            )

        return {
            "vbmeta": vbmeta_info,
            "vbmeta_system": vbmeta_system_info,
            "hash_parts": hash_parts,
            "hashtree_parts": hashtree_parts,
            "chain_parts": cast(List[Tuple[str, int]], vbmeta_info.get("chain_partitions", [])),
        }

    def _get_partition_info_path(self) -> Path:
        return Path(f"devices/{self.ctx.stock_rom_code}/partition_info.json")

    def _sync_partition_info_from_stock_avb(self, profile: Dict[str, Any]) -> None:
        """Update devices/<code>/partition_info.json with AVB-related stock data."""
        if not profile:
            return

        partition_info_path = self._get_partition_info_path()
        partition_info_path.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {}
        if partition_info_path.exists():
            try:
                payload = cast(
                    Dict[str, Any], json.loads(partition_info_path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, OSError):
                payload = {}

        payload.setdefault("device_code", self.ctx.stock_rom_code)
        payload.setdefault("super_size", self._get_super_size())
        dynamic_partitions = cast(
            List[str], payload.get("dynamic_partitions", self._get_partition_list())
        )
        payload["dynamic_partitions"] = dynamic_partitions

        hash_parts = set(cast(set[str], profile.get("hash_parts", set())))
        hashtree_parts = set(cast(set[str], profile.get("hashtree_parts", set())))
        chain_parts = cast(List[Tuple[str, int]], profile.get("chain_parts", []))
        chain_part_names = {name for name, _loc in chain_parts if name in {"boot", "recovery"}}
        strict_parts = sorted(
            ((hash_parts | hashtree_parts) - set(dynamic_partitions)) | chain_part_names
        )

        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        stock_images_dir = Path("build/stockrom/images")
        physical_partition_sizes = cast(Dict[str, int], payload.get("physical_partition_sizes", {}))
        for part in strict_parts:
            image = stock_images_dir / f"{part}.img"
            if not image.exists():
                continue
            info = self._run_avbtool_info_image(avbtool, image) or {}
            image_size = cast(Optional[int], info.get("image_size"))
            physical_partition_sizes[part] = int(image_size or image.stat().st_size)

        payload["physical_partition_sizes"] = dict(sorted(physical_partition_sizes.items()))
        payload["avb_hash_partitions"] = sorted(hash_parts)
        payload["avb_hashtree_partitions"] = sorted(hashtree_parts)
        payload["avb_chain_partitions"] = [
            {"name": name, "rollback_index_location": loc} for name, loc in chain_parts
        ]
        payload["avb_strict_partitions"] = strict_parts

        partition_info_path.write_text(
            json.dumps(payload, indent=4, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.logger.info("Updated %s with stock AVB partition data.", partition_info_path)

    def _calc_avb_max_image_size(self, avbtool: Path, footer_cmd: str, partition_size: int) -> int:
        output = subprocess.check_output(
            [
                str(avbtool),
                footer_cmd,
                "--partition_size",
                str(partition_size),
                "--calc_max_image_size",
            ],
            text=True,
            stderr=subprocess.STDOUT,
            env=self._avb_env(),
        ).strip()
        return int(output)

    def _try_calc_avb_max_image_size(
        self, avbtool: Path, footer_cmd: str, partition_size: int
    ) -> Optional[int]:
        try:
            return self._calc_avb_max_image_size(avbtool, footer_cmd, partition_size)
        except (subprocess.SubprocessError, ValueError, TypeError):
            return None

    def _calculate_min_partition_size_for_image(
        self, avbtool: Path, footer_cmd: str, image_size: int
    ) -> int:
        """Binary-search minimum partition_size so AVB can hold the current image."""
        block = 4096
        lo = max(block, ((image_size + block - 1) // block) * block)
        hi = lo

        max_size = self._try_calc_avb_max_image_size(avbtool, footer_cmd, hi)
        attempts = 0
        max_attempts = 32
        while max_size is None or max_size < image_size:
            hi *= 2
            max_size = self._try_calc_avb_max_image_size(avbtool, footer_cmd, hi)
            attempts += 1
            if attempts > max_attempts:
                raise RuntimeError(
                    "Failed to find valid partition_size for "
                    f"{footer_cmd} image_size={image_size}, last_try={hi}"
                )

        while lo < hi:
            mid = ((lo + hi) // (2 * block)) * block
            if mid <= 0:
                mid = block
            max_size = self._try_calc_avb_max_image_size(avbtool, footer_cmd, mid)
            if max_size is not None and max_size >= image_size:
                hi = mid
            else:
                lo = mid + block

        return hi

    def _apply_avb_to_custom_images(self, partition_list: List[str]) -> None:
        """Add AVB footer for all partitions described by stock AVB profile."""
        profile = self._collect_stock_avb_profile()
        if not profile:
            return

        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        stock_images_dir = Path("build/stockrom/images")
        known_parts = set(partition_list)
        dynamic_partitions = set(self._get_partition_list())
        hash_parts = cast(set[str], profile["hash_parts"])
        hashtree_parts = cast(set[str], profile["hashtree_parts"])
        target_hash_parts = sorted(hash_parts & known_parts)
        target_hashtree_parts = sorted(hashtree_parts & known_parts)
        chain_parts = cast(List[Tuple[str, int]], profile.get("chain_parts", []))
        chain_part_names = [name for name, _loc in chain_parts if name in {"boot", "recovery"}]
        strict_physical_caps = ((hash_parts | hashtree_parts) - dynamic_partitions) | set(
            chain_part_names
        )
        key_path = self._get_avb_testkey_path()

        def trim_trailing_zero_padding(image: Path, max_size: int) -> int:
            current_size = image.stat().st_size
            if current_size <= max_size:
                return current_size
            with open(image, "rb+") as fp:
                fp.seek(max_size)
                tail = fp.read()
                if any(byte != 0 for byte in tail):
                    raise RuntimeError(
                        f"{image.name} ({current_size}) exceeds max AVB payload size {max_size} "
                        "and tail is not zero padding; refusing to truncate."
                    )
                fp.truncate(max_size)
            self.logger.info(
                "Trimmed zero padding for %s: %d -> %d bytes before AVB footer.",
                image.name,
                current_size,
                max_size,
            )
            return max_size

        def resolve_partition_size(part: str, footer_cmd: str, image_size: int) -> int:
            min_partition_size = self._calculate_min_partition_size_for_image(
                avbtool, footer_cmd, image_size
            )
            stock_image = stock_images_dir / f"{part}.img"
            stock_partition_size = stock_image.stat().st_size if stock_image.exists() else 0
            return max(min_partition_size, stock_partition_size)

        def sign_partition(
            part: str,
            footer_cmd: str,
            *,
            with_key: bool = False,
            rollback_index: Optional[int] = None,
        ) -> None:
            image = self.images_out / f"{part}.img"
            if not image.exists():
                return
            image_size = image.stat().st_size
            stock_image = stock_images_dir / f"{part}.img"
            stock_partition_size = stock_image.stat().st_size if stock_image.exists() else 0
            if part in strict_physical_caps:
                # Remove any existing AVB footer first so old larger footer sizing
                # doesn't force this image over the strict physical partition limit.
                try:
                    self.shell.run(
                        [str(avbtool), "erase_footer", "--image", str(image)],
                        env=self._avb_env(),
                    )
                    image_size = image.stat().st_size
                except subprocess.CalledProcessError:
                    pass
            # For critical physical partitions, cap to stock size.
            partition_size = (
                stock_partition_size
                if part in strict_physical_caps and stock_partition_size > 0
                else resolve_partition_size(part, footer_cmd, image_size)
            )

            def build_cmd(part_size: int) -> List[str]:
                cmd = [
                    str(avbtool),
                    footer_cmd,
                    "--image",
                    str(image),
                    "--partition_name",
                    part,
                    "--partition_size",
                    str(part_size),
                ]
                cmd.extend(
                    self._build_footer_props_args(
                        part,
                        include_hash_algorithm=(footer_cmd == "add_hashtree_footer"),
                    )
                )
                if with_key and key_path:
                    algo = self._algorithm_for_key(AVB_DEFAULT_ALGORITHM, key_path)
                    cmd.extend(["--key", str(key_path), "--algorithm", algo])
                if rollback_index is not None:
                    cmd.extend(["--rollback_index", str(rollback_index)])
                return cmd

            cmd = build_cmd(partition_size)
            try:
                self.shell.run(cmd, env=self._avb_env())
                self._avb_partition_size[part] = partition_size
                self.logger.info(
                    "Applied %s footer for AVB partition %s (image=%d, partition=%d)",
                    footer_cmd,
                    part,
                    image_size,
                    partition_size,
                )
            except subprocess.CalledProcessError as e:
                # Retry once for strict partitions by trimming zero padding.
                if part in strict_physical_caps and stock_partition_size > 0:
                    try:
                        max_payload_size = self._calc_avb_max_image_size(
                            avbtool, footer_cmd, partition_size
                        )
                        image_size = trim_trailing_zero_padding(image, max_payload_size)
                        cmd = build_cmd(partition_size)
                        self.shell.run(cmd, env=self._avb_env())
                        self._avb_partition_size[part] = partition_size
                        self.logger.info(
                            "Applied %s footer for AVB partition %s after trim (image=%d, partition=%d)",
                            footer_cmd,
                            part,
                            image_size,
                            partition_size,
                        )
                        return
                    except (subprocess.CalledProcessError, RuntimeError) as retry_err:
                        raise RuntimeError(
                            f"Failed to apply AVB {footer_cmd} for strict partition {part}: {retry_err}"
                        ) from retry_err
                raise RuntimeError(
                    f"Failed to apply AVB {footer_cmd} for custom partition {part}: {e}"
                ) from e

        for part in target_hash_parts:
            sign_partition(part, "add_hash_footer")
        for part in target_hashtree_parts:
            sign_partition(part, "add_hashtree_footer")

        if key_path:
            stock_boot_info = (
                self._run_avbtool_info_image(avbtool, stock_images_dir / "boot.img") or {}
            )
            stock_recovery_info = (
                self._run_avbtool_info_image(avbtool, stock_images_dir / "recovery.img") or {}
            )
            rollback_map = {
                "boot": stock_boot_info.get("rollback_index"),
                "recovery": stock_recovery_info.get("rollback_index"),
            }
            for part in chain_part_names:
                sign_partition(
                    part,
                    "add_hash_footer",
                    with_key=True,
                    rollback_index=cast(Optional[int], rollback_map.get(part)),
                )

    def _extract_avb_public_key(self, avbtool: Path, key_path: Path) -> Path:
        pubkey_path = self.meta_out / "avb_testkey.avbpubkey"
        subprocess.check_output(
            [
                str(avbtool),
                "extract_public_key",
                "--key",
                str(key_path),
                "--output",
                str(pubkey_path),
            ],
            text=True,
            stderr=subprocess.STDOUT,
            env=self._avb_env(),
        )
        return pubkey_path

    def _rebuild_vbmeta_images(self, partition_list: List[str]) -> None:
        """Rebuild vbmeta images so the AVB chain matches repacked images."""
        profile = self._collect_stock_avb_profile()
        if not profile:
            self.logger.info("Skipping vbmeta rebuild: stock AVB profile unavailable.")
            return

        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        key_path = self._get_avb_testkey_path()
        if not avbtool.exists() or not key_path:
            self.logger.info("Skipping vbmeta rebuild: avbtool or signing key unavailable.")
            return

        known_parts = set(partition_list)
        vbmeta_info = cast(Dict[str, Any], profile["vbmeta"])
        vbmeta_system_info = cast(Optional[Dict[str, Any]], profile.get("vbmeta_system"))
        hash_parts = cast(set[str], profile["hash_parts"])
        hashtree_parts = cast(set[str], profile["hashtree_parts"])
        chain_parts = cast(List[Tuple[str, int]], profile["chain_parts"])

        vbmeta_system_parts: List[str] = []
        if vbmeta_system_info:
            vbmeta_system_parts = [
                p
                for p in cast(List[str], vbmeta_system_info.get("hashtree_partitions", []))
                if p in known_parts and (self.images_out / f"{p}.img").exists()
            ]

        if vbmeta_system_parts:
            vbmeta_system_algo = self._algorithm_for_key(
                str((vbmeta_system_info or {}).get("algorithm") or AVB_DEFAULT_ALGORITHM),
                key_path,
            )
            vbmeta_system_img = self.images_out / "vbmeta_system.img"
            cmd = [
                str(avbtool),
                "make_vbmeta_image",
                "--output",
                str(vbmeta_system_img),
                "--key",
                str(key_path),
                "--algorithm",
                vbmeta_system_algo,
            ]
            rollback_index = (vbmeta_system_info or {}).get("rollback_index")
            if rollback_index is not None:
                cmd.extend(["--rollback_index", str(rollback_index)])
            flags = (vbmeta_system_info or {}).get("flags")
            if flags is not None:
                cmd.extend(["--flags", str(flags)])
            for part in vbmeta_system_parts:
                cmd.extend(
                    [
                        "--include_descriptors_from_image",
                        str(self.images_out / f"{part}.img"),
                    ]
                )
            self.shell.run(cmd, env=self._avb_env())
            self.logger.info(
                "Rebuilt vbmeta_system.img with partitions: %s",
                ", ".join(vbmeta_system_parts),
            )

        include_parts = sorted((hash_parts | hashtree_parts) & known_parts)
        if vbmeta_system_parts:
            include_parts = [p for p in include_parts if p not in vbmeta_system_parts]

        pubkey_path = self._extract_avb_public_key(avbtool, key_path)
        chain_entries: List[Tuple[str, int]] = []
        for name, loc in chain_parts:
            if name == "vbmeta_system":
                if not (self.images_out / "vbmeta_system.img").exists():
                    continue
            elif not (self.images_out / f"{name}.img").exists():
                continue
            chain_entries.append((name, loc))

        vbmeta_algo = self._algorithm_for_key(
            str(vbmeta_info.get("algorithm") or AVB_DEFAULT_ALGORITHM),
            key_path,
        )
        vbmeta_img = self.images_out / "vbmeta.img"
        cmd = [
            str(avbtool),
            "make_vbmeta_image",
            "--output",
            str(vbmeta_img),
            "--key",
            str(key_path),
            "--algorithm",
            vbmeta_algo,
        ]
        rollback_index = vbmeta_info.get("rollback_index")
        if rollback_index is not None:
            cmd.extend(["--rollback_index", str(rollback_index)])
        flags = vbmeta_info.get("flags")
        if flags is not None:
            cmd.extend(["--flags", str(flags)])
        for part in include_parts:
            cmd.extend(
                [
                    "--include_descriptors_from_image",
                    str(self.images_out / f"{part}.img"),
                ]
            )
        for name, loc in chain_entries:
            cmd.extend(["--chain_partition", f"{name}:{loc}:{pubkey_path}"])
        self.shell.run(cmd, env=self._avb_env())
        self.logger.info(
            "Rebuilt vbmeta.img with include=%s chain=%s",
            ",".join(include_parts),
            ",".join(f"{name}:{loc}" for name, loc in chain_entries),
        )

    def _verify_avb_images(self) -> None:
        """Verify top-level vbmeta and chained partitions before OTA packaging."""
        vbmeta_img = self.images_out / "vbmeta.img"
        if not vbmeta_img.exists():
            self.logger.info("Skipping AVB verification: vbmeta.img not found in IMAGES.")
            return

        avbtool = self.ota_tools_dir / "bin" / "avbtool"
        if not avbtool.exists():
            raise RuntimeError(f"avbtool not found at {avbtool}, cannot verify AVB chain.")

        cmd = [
            str(avbtool),
            "verify_image",
            "--image",
            str(vbmeta_img),
            "--follow_chain_partitions",
        ]
        self.shell.run(cmd, env=self._avb_env())
        self.logger.info("AVB verification succeeded for vbmeta chain.")

    def _build_avb_misc_lines_from_stock(self, partition_list: List[str]) -> List[str]:
        """Infer AVB-related misc_info lines from stock images."""
        profile = self._collect_stock_avb_profile()
        if not profile:
            return []
        vbmeta_info = cast(Dict[str, Any], profile["vbmeta"])

        stock_images_dir = Path("build/stockrom/images")
        vbmeta_system_info = self._run_avbtool_info_image(
            self.ota_tools_dir / "bin" / "avbtool", stock_images_dir / "vbmeta_system.img"
        )
        boot_info = self._run_avbtool_info_image(
            self.ota_tools_dir / "bin" / "avbtool", stock_images_dir / "boot.img"
        )
        recovery_info = self._run_avbtool_info_image(
            self.ota_tools_dir / "bin" / "avbtool", stock_images_dir / "recovery.img"
        )
        testkey = self._get_avb_testkey_path()
        if not testkey:
            self.logger.warning(
                "AVB testkey not found under otatools; skipping AVB misc_info hints."
            )
            return []
        key_algo = self._algorithm_for_key(AVB_DEFAULT_ALGORITHM, testkey)

        known_parts = set(partition_list)
        lines: List[str] = [
            "avb_enable=true",
            "avb_building_vbmeta_image=true",
            "avb_avbtool=avbtool",
            f"avb_vbmeta_key_path={testkey}",
            f"avb_vbmeta_algorithm={key_algo}",
        ]

        chain_parts = cast(List[Tuple[str, int]], vbmeta_info.get("chain_partitions", []))
        chain_loc_by_name = {name: loc for name, loc in chain_parts}

        if vbmeta_system_info:
            vbmeta_system_hashtree_parts = cast(
                List[str], vbmeta_system_info.get("hashtree_partitions", [])
            )
            vbmeta_system_parts = [p for p in vbmeta_system_hashtree_parts if p in known_parts]
            if vbmeta_system_parts:
                lines.append(f"avb_vbmeta_system={' '.join(vbmeta_system_parts)}")
                lines.append(f"avb_vbmeta_system_key_path={testkey}")
                lines.append(f"avb_vbmeta_system_algorithm={key_algo}")
                if vbmeta_system_info.get("rollback_index") is not None:
                    lines.append(
                        f"avb_vbmeta_system_rollback_index={vbmeta_system_info['rollback_index']}"
                    )
            if "vbmeta_system" in chain_loc_by_name:
                lines.append(
                    "avb_vbmeta_system_rollback_index_location="
                    f"{chain_loc_by_name['vbmeta_system']}"
                )

        for part, part_info in (("boot", boot_info), ("recovery", recovery_info)):
            if part not in known_parts:
                continue
            if part_info:
                lines.append(f"avb_{part}_algorithm={key_algo}")
                if part_info.get("rollback_index") is not None:
                    lines.append(f"avb_{part}_rollback_index={part_info['rollback_index']}")
            else:
                lines.append(f"avb_{part}_algorithm={key_algo}")
            lines.append(f"avb_{part}_key_path={testkey}")
            if part in chain_loc_by_name:
                lines.append(f"avb_{part}_rollback_index_location={chain_loc_by_name[part]}")
            add_hash_args = self._build_footer_props_args(part, include_hash_algorithm=False)
            if part_info and part_info.get("rollback_index") is not None:
                add_hash_args.extend(["--rollback_index", str(part_info["rollback_index"])])
            if add_hash_args:
                lines.append(f"avb_{part}_add_hash_footer_args={' '.join(add_hash_args)}")

        hash_parts = set(cast(List[str], profile.get("hash_parts", [])))
        hashtree_parts = set(cast(List[str], profile.get("hashtree_parts", [])))
        if vbmeta_system_info:
            hashtree_parts.update(
                cast(List[str], vbmeta_system_info.get("hashtree_partitions", []))
            )

        custom_parts = sorted(((hash_parts | hashtree_parts) - AOSP_AVB_PARTITIONS) & known_parts)
        if custom_parts:
            lines.append(f"avb_custom_images_partition_list={' '.join(custom_parts)}")
            for part in custom_parts:
                lines.append(f"avb_{part}_image_list={part}.img")

        for part in sorted(hash_parts & known_parts):
            lines.append(f"avb_{part}_hash_enable=true")
            image = self.images_out / f"{part}.img"
            if part in self._avb_partition_size:
                lines.append(f"avb_{part}_partition_size={self._avb_partition_size[part]}")
            elif image.exists():
                lines.append(f"avb_{part}_partition_size={image.stat().st_size}")
            add_hash_args = self._build_footer_props_args(part, include_hash_algorithm=False)
            if add_hash_args:
                lines.append(f"avb_{part}_add_hash_footer_args={' '.join(add_hash_args)}")

        for part in sorted(hashtree_parts & known_parts):
            lines.append(f"avb_{part}_hashtree_enable=true")
            image = self.images_out / f"{part}.img"
            if part in self._avb_partition_size:
                lines.append(f"avb_{part}_partition_size={self._avb_partition_size[part]}")
            elif image.exists():
                lines.append(f"avb_{part}_partition_size={image.stat().st_size}")
            add_hashtree_args = self._build_footer_props_args(part, include_hash_algorithm=True)
            if add_hashtree_args:
                lines.append(f"avb_{part}_add_hashtree_footer_args={' '.join(add_hashtree_args)}")

        return lines

    def _generate_meta_info(self) -> None:
        """Generate ab_partitions.txt, dynamic_partitions_info.txt, misc_info.txt"""
        self.logger.info("Generating META info...")
        self.meta_out.mkdir(parents=True, exist_ok=True)
        partition_list: List[str] = [
            img.stem for img in self.images_out.glob("*.img") if img.stem != "cust"
        ]
        with open(self.meta_out / "ab_partitions.txt", "w") as f:
            for p in sorted(partition_list):
                f.write(f"{p}\n")

        super_size: int = self._get_super_size()
        self.logger.info(
            "Current packing super_size: %d bytes (%.2f GiB)",
            super_size,
            super_size / (1024**3),
        )
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

        misc_lines = [
            "recovery_api_version=3",
            "fstab_version=2",
            "ab_update=true",
        ]
        misc_lines.extend(self._build_avb_misc_lines_from_stock(partition_list))

        if self._is_virtual_ab_compression_enabled():
            misc_lines.append("virtual_ab_compression=true")
            metadata = self._get_dynamic_partition_metadata()
            compression_method = (
                metadata.get("vabc_compression_param", "lz4") if metadata else "lz4"
            )
            cow_version = metadata.get("cow_version", 3) if metadata else 3
            compression_factor = metadata.get("compression_factor", 65536) if metadata else 65536
            misc_lines.append(f"virtual_ab_compression_method={compression_method}")
            misc_lines.append(f"virtual_ab_cow_version={cow_version}")
            misc_lines.append(f"virtual_ab_compression_factor={compression_factor}")
            self.logger.info(
                f"Virtual A/B compression enabled: method={compression_method}, "
                f"cow_version={cow_version}, factor={compression_factor}"
            )

        with open(self.meta_out / "misc_info.txt", "w") as f:
            f.write("\n".join(dict.fromkeys(misc_lines)) + "\n")
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
            prefix = build_rom_filename_prefix(self.ctx)
            device_tag = build_rom_filename_device_tag(self.ctx)
            final_path: Path = (
                self.out_dir
                / f"{prefix}{device_tag}-ota_full-{self.ctx.target_rom_version}-{self.ctx.security_patch}-{timestamp}-{md5}-{self.ctx.port_android_version}.zip"
            )
            output_zip.rename(final_path)
            self.logger.info(f"Final OTA Package: {final_path}")
        except (subprocess.CalledProcessError, OSError) as e:
            self.logger.error(f"OTA generation failed: {e}")
