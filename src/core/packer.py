import concurrent.futures
import hashlib
import os
import time
import math
import logging
import shutil
import subprocess
from pathlib import Path
from src.utils.shell import ShellRunner
from src.utils.fspatch import patch_fs_config
from src.utils.contextpatch import ContextPatcher
from datetime import datetime

class Repacker:
    def __init__(self, context):
        """
        :param context: PortingContext object containing target_dir and other info
        """
        self.ctx = context
        self.logger = logging.getLogger("Packer")
        self.shell = ShellRunner()
        
        # Define tool paths (assumed in bin directory or system commands)
        self.bin_dir = Path("bin").resolve()
       
        self.selinux_patcher = ContextPatcher()
        # Fixed timestamp from shell script
        self.fix_timestamp = "1230768000"
        # Define OTA output directory structure
        self.out_dir = Path("out").resolve()
        self.product_out = self.out_dir / "target" / "product" / self.ctx.stock_rom_code
        self.images_out = self.product_out / "IMAGES"
        self.meta_out = self.product_out / "META"
        self.ota_tools_dir = Path("otatools").resolve()

    def pack_all(self, pack_type="EROFS", is_rw=False):
        """
        Pack all partitions under target directory (parallel optimization)
        :param pack_type: "EXT" (ext4) or "EROFS"
        :param is_rw: Read-write mode (only valid for EXT4)
        """
        self.logger.info(f"Starting repack with format: {pack_type}")
        
        # Get list of partitions to pack (exclude config and repack_images)
        partitions = []
        for item in self.ctx.target_dir.iterdir():
            if item.is_dir() and item.name not in ["config", "repack_images"]:
                partitions.append(item.name)
        
        # Use ThreadPoolExecutor for parallel packing
        #max_workers = os.cpu_count() // 2 if os.cpu_count() > 4 else 2
        max_workers = 4 # Limit concurrent workers to prevent overload
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for part_name in partitions:
                futures.append(
                    executor.submit(self._pack_partition, part_name, pack_type, is_rw)
                )
            
            # Wait for all tasks to complete and capture exceptions
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Partition packing failed: {e}")
                    raise

    def _pack_partition(self, part_name, pack_type, is_rw):
        src_dir = self.ctx.target_dir / part_name
        img_output = self.ctx.target_dir / f"{part_name}.img"
        fs_config = self.ctx.target_config_dir / f"{part_name}_fs_config"
        file_contexts = self.ctx.target_config_dir / f"{part_name}_file_contexts"

        self.logger.info(f"Packing [{part_name}] as {pack_type}...")

        self._run_patch_tools(src_dir, fs_config, file_contexts)

        if pack_type == "EXT":
            self._pack_ext4(part_name, src_dir, img_output, fs_config, file_contexts, is_rw)
        else:
            self._pack_erofs(part_name, src_dir, img_output, fs_config, file_contexts)

    def _run_patch_tools(self, src_dir, fs_config, file_contexts):
        """Call patching tools from utils"""
        
        if fs_config.exists():
            try:
                patch_fs_config(src_dir, fs_config)
            except Exception as e:
                self.logger.error(f"Error patching fs_config: {e}")
        else:
            self.logger.warning(f"fs_config not found for {src_dir.name}, skipping fspatch.")

        if file_contexts.exists():
            try:
                self.selinux_patcher.patch(src_dir, file_contexts)
            except Exception as e:
                self.logger.error(f"Error patching file_contexts: {e}")
        else:
            self.logger.warning(f"file_contexts not found for {src_dir.name}, skipping contextpatch.")
    
    def _pack_erofs(self, part_name, src_dir, img_output, fs_config, file_contexts):
        """Pack EROFS image"""
        cmd = [
            "mkfs.erofs",
            "-zlz4hc,9",
            "-T", self.fix_timestamp,
            "--mount-point", f"/{part_name}",
            "--fs-config-file", str(fs_config),
            "--file-contexts", str(file_contexts),
            str(img_output),
            str(src_dir)
        ]
        try:
            self.shell.run(cmd)
            self.logger.info(f"Successfully packed {part_name}.img (EROFS)")
        except Exception as e:
            self.logger.error(f"Failed to pack {part_name}: {e}")

    def _pack_ext4(self, part_name, src_dir, img_output, fs_config, file_contexts, is_rw):
        """Pack EXT4 image with size calculation and regeneration"""
        
        # A. Calculate directory size (du -sb)
        size_orig = self._get_dir_size(src_dir)
        
        # B. Calculate target size
        if size_orig < 1048576:  # 1MB
            size = 1048576
        elif size_orig < 104857600: # 100MB
            size = int(size_orig * 1.15)
        elif size_orig < 1073741824: # 1GB
            size = int(size_orig * 1.08)
        else:
            size = int(size_orig * 1.03)
        
        # Align to 4K
        size = (size // 4096) * 4096
        
        # C. Prepare lost+found
        lost_found = src_dir / "lost+found"
        lost_found.mkdir(exist_ok=True)

        # D. Calculate Inode count
        try:
            with open(fs_config, 'r') as f:
                inode_count = sum(1 for _ in f) + 8
        except:
            inode_count = 5000 # Fallback

        # E. First generation
        self._make_ext4_image(part_name, src_dir, img_output, size, inode_count, fs_config, file_contexts, is_rw)

        # F. Shrink size (resize2fs -M)
        self.shell.run(["resize2fs", "-f", "-M", str(img_output)])

        # G. Calculate remaining space and decide if second pack is needed
        # mi_ext does not undergo second pack
        if part_name == "mi_ext":
            return

        # Get Free blocks after resize
        free_blocks = self._get_free_blocks(img_output)
        
        # If there is free space and not Readaw
        if free_blocks > 0:
            free_size = free_blocks * 4096
            current_img_size = img_output.stat().st_size
            
            # Calculate new compact size
            new_size = current_img_size - free_size
            new_size = (new_size // 4096) * 4096
            
            self.logger.info(f"Regenerating {part_name}.img with optimized size: {new_size}")
            img_output.unlink() # Delete old
            
            # Second generation
            self._make_ext4_image(part_name, src_dir, img_output, new_size, inode_count, fs_config, file_contexts, is_rw)
            self.shell.run(["resize2fs", "-f", "-M", str(img_output)])

    def _make_ext4_image(self, part_name, src_dir, img_path, size, inodes, fs_config, file_contexts, is_rw):
        """Execute mke2fs and e2fsdroid"""
        # 1. mke2fs (create empty image)
        mkfs_cmd = [
            "mke2fs", "-O", "^has_journal",
            "-L", part_name,
            "-I", "256",
            "-N", str(inodes),
            "-M", f"/{part_name}",
            "-m", "0", "-t", "ext4", "-b", "4096",
            str(img_path),
            str(size // 4096) + "K"
        ]
        mkfs_cmd[-1] = str(size // 4096)
        
        self.shell.run(mkfs_cmd)

        # 2. e2fsdroid (write files)
        e2fs_cmd = [
            "e2fsdroid", "-e",
            "-T", self.fix_timestamp,
            "-C", str(fs_config),
            "-S", str(file_contexts),
            "-f", str(src_dir),
            "-a", f"/{part_name}",
            str(img_path)
        ]
        
        # If not RW mode, add -s (share_dupe)
        if not is_rw:
            e2fs_cmd.insert(-1, "-s")
            
        self.shell.run(e2fs_cmd)

    def _get_dir_size(self, path):
        """
        Calculate directory size using du -sb (much faster than Python rglob)
        """
        try:
            output = subprocess.check_output(["du", "-sb", str(path)], text=True)
            return int(output.split()[0])
        except Exception as e:
            self.logger.warning(f"du command failed, falling back to python: {e}")
            total = 0
            for p in path.rglob('*'):
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            return total if total > 0 else 4096

    def _get_free_blocks(self, img_path):
        """Parse tune2fs -l output to get Free blocks"""
        try:
            output = subprocess.check_output(["tune2fs", "-l", str(img_path)], text=True)
            for line in output.splitlines():
                if "Free blocks:" in line:
                    return int(line.split(":")[1].strip())
        except:
            return 0
        return 0
    
        return 0
    
    def pack_super_image(self):
        """
        Pack super.img for non-payload.bin ROMs
        """
        self.logger.info("Packing super.img...")
        
        # 1. Define paths
        lpmake_path = self.ota_tools_dir / "bin" / "lpmake"
        if not lpmake_path.exists():
             self.logger.error(f"lpmake not found at {lpmake_path}")
             return

        super_img = self.ctx.target_dir / "super.img"
        super_size = self._get_super_size()
        
        # 2. Base arguments
        # --metadata-size 65536 --super-name super --block-size 4096
        base_args = [
            str(lpmake_path),
            "--metadata-size", "65536",
            "--super-name", "super",
            "--block-size", "4096",
            "--device", f"super:{super_size}",
            "--output", str(super_img)
        ]

        # 3. Handle A-only vs V-AB
        is_ab = self.ctx.is_ab_device
        
        if not is_ab:
            self.logger.info("Packing A-only super.img")
            # --metadata-slots 2 --group=qti_dynamic_partitions:$superSize
            base_args.extend(["--metadata-slots", "2"])
            base_args.extend(["--group", f"qti_dynamic_partitions:{super_size}"])
            base_args.append("-F") # Sparse

            # Iterate partitions
            # List from shell script: odm mi_ext system system_ext product vendor
            # But we should scan what we have
            partitions = ["odm", "mi_ext", "system", "system_ext", "product", "vendor", "odm_dlkm", "vendor_dlkm", "system_dlkm", "product_dlkm"]
            
            for part in partitions:
                img_path = self.ctx.target_dir / f"{part}.img"
                if img_path.exists():
                    size = img_path.stat().st_size
                    self.logger.info(f"Partition [{part}]: {size} bytes")
                    # --partition name:attributes:size:group --image name=path
                    # attributes: none (or readonly)
                    base_args.extend([
                        "--partition", f"{part}:none:{size}:qti_dynamic_partitions",
                        "--image", f"{part}={img_path}"
                    ])
        else:
            self.logger.info("Packing V-AB super.img")
            # --virtual-ab --metadata-slots 3
            # --group=qti_dynamic_partitions_a:$superSize --group=qti_dynamic_partitions_b:$superSize
            base_args.extend(["--virtual-ab", "--metadata-slots", "3"])
            base_args.extend(["--group", f"qti_dynamic_partitions_a:{super_size}"])
            base_args.extend(["--group", f"qti_dynamic_partitions_b:{super_size}"])
            base_args.append("-F")

            # Scan partitions
            # Use super_list from context if available, or scan standard names
            partitions = ["odm", "mi_ext", "system", "system_ext", "product", "vendor", "odm_dlkm", "vendor_dlkm", "system_dlkm", "product_dlkm"]
            
            for part in partitions:
                img_path = self.ctx.target_dir / f"{part}.img"
                if img_path.exists():
                    size = img_path.stat().st_size
                    self.logger.info(f"Partition [{part}]: {size} bytes")
                    # --partition name_a:none:size:group_a --image name_a=path
                    # --partition name_b:none:0:group_b
                    base_args.extend([
                        "--partition", f"{part}_a:none:{size}:qti_dynamic_partitions_a",
                        "--image", f"{part}_a={img_path}",
                        "--partition", f"{part}_b:none:0:qti_dynamic_partitions_b"
                    ])

        # 4. Run lpmake
        try:
            self.shell.run(base_args)
            self.logger.info("super.img generated successfully.")
        except Exception as e:
            self.logger.error(f"Failed to generate super.img: {e}")
            return

        # 5. Compress to super.zst
        self.logger.info("Compressing super.img to super.zst...")
        zst_path = self.ctx.target_dir / "super.zst"
        try:
            # Try to use zstd from system, or bin/zstd
            # Assume system zstd is available or copy it
            self.shell.run(["zstd", "--rm", str(super_img), "-o", str(zst_path)])
            self.logger.info("Compressed super.zst generated.")
        except Exception as e:
             self.logger.warning(f"zstd compression failed: {e}. Keeping super.img")
             # Fallback: if zstd fails, keep super.img? 
             # The flash script expects super.zst usually.
        
        # 6. Generate Flashing Script (Output folder)
        self._generate_flash_script(zst_path if zst_path.exists() else super_img)

    def _generate_flash_script(self, super_image_path):
        """
        Generate fastboot flash scripts (Windows/Linux/Mac)
        Ref: port.sh lines 1733+
        """
        self.logger.info("Generating flashing scripts...")
        
        # Prepare output directory
        # out/{os_type}_{device_code}_{port_rom_version}
        # Simplified: out/{device_code}_{port_rom_version}_fastboot
        out_name = f"{self.ctx.stock_rom_code}_{self.ctx.target_rom_version}_fastboot"
        out_path = self.out_dir / out_name
        
        if out_path.exists():
            shutil.rmtree(out_path)
        out_path.mkdir(parents=True, exist_ok=True)
        
        bin_windows = out_path / "bin/windows"
        bin_windows.mkdir(parents=True, exist_ok=True)
        
        firmware_update = out_path / "firmware-update"
        firmware_update.mkdir(parents=True, exist_ok=True)

        # 1. Copy super image
        self.logger.info(f"Copying {super_image_path.name}...")
        shutil.copy2(super_image_path, out_path / super_image_path.name)
        
        # 2. Copy firmware images
        # Repacker uses self.ctx.repack_images_dir
        if self.ctx.repack_images_dir.exists():
            for fw in self.ctx.repack_images_dir.glob("*.img"):
                 shutil.copy2(fw, firmware_update)
        
        # 3. Copy tools (fastboot, scripts)
        # Assuming we have a template folder bin/flash in project root
        # If not, we might need to create dummy scripts or download tools
        flash_template = Path("bin/flash")
        
        if flash_template.exists():
             # Windows tools
             if (flash_template / "platform-tools-windows").exists():
                 shutil.copytree(flash_template / "platform-tools-windows", bin_windows, dirs_exist_ok=True)
             
             # Scripts
             for script in ["windows_flash_script.bat", "mac_linux_flash_script.sh"]:
                 src_script = flash_template / script
                 if src_script.exists():
                     dest_script = out_path / script
                     shutil.copy2(src_script, dest_script)
                     
                     # Update script content based on A/B status
                     if not self.ctx.is_ab_device:
                         self._patch_script_for_a_only(dest_script)
                     
                     # Update script for firmware flashing
                     self._patch_script_for_firmware(dest_script, firmware_update)

        self.logger.info(f"Fastboot ROM generated at: {out_path}")

    def _patch_script_for_a_only(self, script_path):
        """Remove _a/_b references for A-only devices"""
        content = script_path.read_text(encoding='utf-8', errors='ignore')
        
        # Simple replacements
        content = content.replace("_a", "")
        content = content.replace("_b", "") # Careful not to break other things, but usually safe for partition names
        
        # Remove lines containing "_b" if it's a specific flash command?
        # Shell script uses sed '/_b/d' which deletes lines with _b
        lines = content.splitlines()
        new_lines = [line for line in lines if "_b" not in line]
        
        # Handle specific blocks (SET_ACTION_SLOT_A_BEGIN...)
        # This requires more complex parsing or just simple line filtering
        
        script_path.write_text("\n".join(new_lines), encoding='utf-8')

    def _patch_script_for_firmware(self, script_path, firmware_dir):
        """Inject firmware flash commands"""
        # Read firmware files
        fw_files = [f.name for f in firmware_dir.glob("*")]
        if not fw_files: return
        
        content = script_path.read_text(encoding='utf-8', errors='ignore')
        
        # Generate insertion block
        is_windows = script_path.suffix == ".bat"
        insertion = []
        
        for fw in fw_files:
            # Map filename to partition name
            # mapping logic from port.sh lines 1761+
            part = fw.split('.')[0] # Default
            if fw == "uefi_sec.mbn": part = "uefisecapp"
            elif fw == "qupv3fw.elf": part = "qupfw"
            elif fw == "NON-HLOS.bin": part = "modem"
            elif fw == "km4.mbn": part = "keymaster"
            elif fw == "BTFM.bin": part = "bluetooth"
            elif fw == "dspso.bin": part = "dsp"
            
            # Skip dtbo/cust if needed (port.sh line 1759)
            if "dtbo" in fw or "cust" in fw: continue

            if self.ctx.is_ab_device:
                 if is_windows:
                     insertion.append(f"bin\\windows\\fastboot.exe flash {part}_a %~dp0firmware-update\\{fw}")
                     insertion.append(f"bin\\windows\\fastboot.exe flash {part}_b %~dp0firmware-update\\{fw}")
                 else:
                     insertion.append(f"fastboot flash {part}_a firmware-update/{fw}")
                     insertion.append(f"fastboot flash {part}_b firmware-update/{fw}")
            else:
                 # A-only
                 if is_windows:
                     insertion.append(f"bin\\windows\\fastboot.exe flash {part} %~dp0firmware-update\\{fw}")
                 else:
                     insertion.append(f"fastboot flash {part} firmware-update/{fw}")

        # Insert after "# firmware" marker
        marker = "REM firmware" if is_windows else "# firmware"
        
        if marker in content:
            parts = content.split(marker)
            new_content = parts[0] + marker + "\n" + "\n".join(insertion) + parts[1]
            script_path.write_text(new_content, encoding='utf-8')

    def pack_ota_payload(self):
        """
        Pack AOSP OTA payload (generate payload.bin zip)
        """
        self.logger.info("Starting OTA Payload packing...")

        if self.product_out.exists():
            shutil.rmtree(self.product_out)
        
        self.images_out.mkdir(parents=True, exist_ok=True)
        self.meta_out.mkdir(parents=True, exist_ok=True)
        
        for part in ["SYSTEM", "SYSTEM_EXT", "PRODUCT", "VENDOR", "ODM", "MI_EXT"]:
            (self.product_out / part).mkdir(exist_ok=True)

        self.logger.info("Collecting logical partition images...")
        for img in self.ctx.target_dir.glob("*.img"):
            shutil.copy2(img, self.images_out)

        self.logger.info("Collecting firmware images...")
        if self.ctx.repack_images_dir.exists():
            for img in self.ctx.repack_images_dir.glob("*.img"):
                shutil.copy2(img, self.images_out)

        device_custom_dir = Path(f"devices/{self.ctx.stock_rom_code}")
        if device_custom_dir.exists():
            # Handle boot/dtbo replacement
            ksu_boot = list(device_custom_dir.glob("boot*.img"))
            if ksu_boot:
                shutil.copy2(ksu_boot[0], self.images_out / "boot.img")
                self.logger.info(f"Replaced boot.img with {ksu_boot[0].name}")
            
            dtbo = list(device_custom_dir.glob("dtbo*.img"))
            if dtbo:
                shutil.copy2(dtbo[0], self.images_out / "dtbo.img")

            # Handle recovery
            rec = device_custom_dir / "recovery.img"
            if rec.exists():
                shutil.copy2(rec, self.images_out)

            # Handle init_boot
            init_boot = device_custom_dir / "init_boot-kernelsu.img"
            if init_boot.exists():
                shutil.copy2(init_boot, self.images_out / "init_boot.img")

        # Generate META info
        self._generate_meta_info()

        # Copy build.prop to corresponding directories (for OTA tool to read fingerprint info)
        self._copy_build_props()

        # Call ota_from_target_files
        self._run_ota_tool()

    def _generate_meta_info(self):
        """Generate ab_partitions.txt, dynamic_partitions_info.txt, misc_info.txt"""
        self.logger.info("Generating META info...")

        # --- ab_partitions.txt ---
        ab_txt = self.meta_out / "ab_partitions.txt"
        partition_list = []
        
        # Scan all img under IMAGES
        for img in self.images_out.glob("*.img"):
            if img.stem == "cust": continue
            partition_list.append(img.stem)
        
        with open(ab_txt, "w") as f:
            for p in sorted(partition_list):
                f.write(f"{p}\n")

        # --- dynamic_partitions_info.txt ---
        super_size = self._get_super_size() 
        group_size = super_size - 1048576 # Reserve 1MB

        super_parts = [p for p in partition_list if p in ["system", "vendor", "product", "system_ext", "odm", "mi_ext", "odm_dlkm", "vendor_dlkm", "system_dlkm", "product_dlkm"]]
        super_parts_str = " ".join(super_parts)

        dyn_txt = self.meta_out / "dynamic_partitions_info.txt"
        with open(dyn_txt, "w") as f:
            f.write(f"super_partition_size={super_size}\n")
            f.write(f"super_partition_groups=qti_dynamic_partitions\n")
            f.write(f"super_qti_dynamic_partitions_group_size={group_size}\n")
            f.write(f"super_qti_dynamic_partitions_partition_list={super_parts_str}\n")
            f.write(f"virtual_ab=true\n")
            f.write(f"virtual_ab_compression=true\n")

        # --- misc_info.txt ---
        misc_txt = self.meta_out / "misc_info.txt"
        with open(misc_txt, "w") as f:
            f.write("recovery_api_version=3\n")
            f.write("fstab_version=2\n")
            f.write("ab_update=true\n")

        # --- update_engine_config.txt ---
        ue_txt = self.meta_out / "update_engine_config.txt"
        with open(ue_txt, "w") as f:
            f.write("PAYLOAD_MAJOR_VERSION=2\n")
            f.write("PAYLOAD_MINOR_VERSION=8\n")

    def _copy_build_props(self):
        """Copy build.prop of each partition to directories required by META structure"""

        mapping = {
            "system": "SYSTEM",
            "product": "PRODUCT",
            "system_ext": "SYSTEM_EXT",
            "vendor": "VENDOR",
            "odm": "ODM"
        }
        
        for part_lower, part_upper in mapping.items():
            # First try to find in unpacked directory
            src_prop = self.ctx.get_target_prop_file(part_lower)
            if src_prop and src_prop.exists():
                shutil.copy2(src_prop, self.product_out / part_upper / "build.prop")
            else:
                self.logger.warning(f"build.prop for {part_lower} not found, OTA metadata might be incomplete.")

    def _run_ota_tool(self):
        """Call ota_from_target_files to generate ZIP"""
        self.logger.info("Running ota_from_target_files...")
        
        # Construct output filename
        now = datetime.now()

        # Format to specified string structure
        timestamp = now.strftime("%Y%m%d%H%M%S")
        output_zip = self.out_dir / f"{self.ctx.stock_rom_code}-ota_full-{timestamp}.zip"
        
        key_path = self.ota_tools_dir / "security" / "testkey"
        
        # Simple check if key exists
        if not (self.ota_tools_dir / "security" / "testkey.pk8").exists():
            self.logger.warning(f"Signature key not found at {key_path}.pk8! Please check your otatools/security folder.")

        custom_tmp_dir = self.out_dir / "tmp"

        if custom_tmp_dir.exists():
            shutil.rmtree(custom_tmp_dir)
        custom_tmp_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger.info(f"Using custom TMPDIR: {custom_tmp_dir}")
        env = os.environ.copy()
        env["PATH"] = f"{self.ota_tools_dir}/bin:{env['PATH']}"
        
        env["TMPDIR"] = str(custom_tmp_dir)

        cmd = [
            str(self.ota_tools_dir / "bin" / "ota_from_target_files"),
            "-v", 
            "-k", str(key_path),
            str(self.product_out),
            str(output_zip)
        ]
        
        try:
            self.shell.run(cmd, env=env)
            self.logger.info(f"OTA Zip generated: {output_zip}")
            
            md5 = hashlib.md5(open(output_zip, 'rb').read()).hexdigest()[:10]
            
            final_name = f"{self.ctx.stock_rom_code}-ota_full-{self.ctx.target_rom_version}-{timestamp}-{md5}-{self.ctx.port_android_version}.zip"
            final_path = self.out_dir / final_name
            output_zip.rename(final_path)
            self.logger.info(f"Final OTA Package: {final_path}")
            
        except Exception as e:
            self.logger.error(f"OTA generation failed: {e}")

    def _get_super_size(self):
        """
        Get Super partition size
        Logic ported from bin/getSuperSize.sh
        """

        device_code = self.ctx.stock_rom_code.upper()
        
        self.logger.info(f"Determining Super partition size for device: {device_code}")

        size_map = {
            # Xiaomi 13 Series / Note 12 Turbo / K60 Pro / MIX Fold 3
            9663676416: ["FUXI", "NUWA", "ISHTAR", "MARBLE", "SOCRATES", "BABYLON"],
            
            # Redmi Note 12 5G 
            9122611200: ["SUNSTONE"],
            
            # Pad 6 Max
            11811160064: ["YUDI"],
        }

        for size, devices in size_map.items():
            if device_code in devices:
                self.logger.info(f"Matched known device {device_code}, size: {size}")
                return size

        # Default size for other devices
        default_size = 9126805504
        self.logger.info(f"Device {device_code} not in special list, using default size: {default_size}")
        
        return default_size
