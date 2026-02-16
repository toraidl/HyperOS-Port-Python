import shutil
import logging
import concurrent.futures
from pathlib import Path
import platform
from types import SimpleNamespace
from src.core.rom import RomPackage
from src.utils.sync_engine import ROMSyncEngine
from src.utils.shell import ShellRunner

class PortingContext:
    def __init__(self, stock_rom: RomPackage, port_rom: RomPackage, target_work_dir: str | Path):
        self.stock = stock_rom
        self.port = port_rom
        self.project_root = Path(".").resolve() # Project root
        self.bin_root = self.project_root / "bin"
        self.target_dir = Path(target_work_dir).resolve() # build/target/
        self.target_config_dir = self.target_dir / "config"
        self.repack_images_dir = self.target_dir / "repack_images"
        self.stock_rom_dir = self.stock.extracted_dir
        self.target_rom_dir = self.target_dir
        self.logger = logging.getLogger("Context")
        self._init_tools()
        self.syncer = ROMSyncEngine(self, logging.getLogger("SyncEngine"))
        self.shell = ShellRunner()
        self.enable_ksu = False

    def _init_tools(self):
        """
        Auto-detect system environment and set global tool paths.
        """
        system = platform.system().lower()   # windows, linux, darwin
        machine = platform.machine().lower() # x86_64, amd64, aarch64, arm64

        # 1. Unify architecture name
        if machine in ["amd64", "x86_64"]:
            arch = "x86_64"
        elif machine in ["aarch64", "arm64"]:
            arch = "arm64"
        else:
            arch = "x86_64" # Default fallback

        # 2. Determine platform directory and extension
        if system == "windows":
            plat_dir = "windows"
            exe_ext = ".exe"
        elif system == "linux":
            plat_dir = "linux"
            exe_ext = ""
        elif system == "darwin":
            plat_dir = "macos"
            exe_ext = ""
        else:
            self.logger.warning(f"Unknown system: {system}, defaulting to Linux.")
            plat_dir = "linux"
            exe_ext = ""

        # 3. Set platform specific bin directory (e.g. bin/linux/x86_64)
        self.platform_bin_dir = self.bin_root / plat_dir / arch
        
        if not self.platform_bin_dir.exists():
            # Try fallback to bin/linux
            fallback = self.bin_root / plat_dir
            if fallback.exists():
                self.platform_bin_dir = fallback

        self.logger.info(f"Platform Binary Dir: {self.platform_bin_dir}")

        # 4. Define global tools (self.tools)
        self.tools = SimpleNamespace()
        
        # >> Native tools
        self.tools.magiskboot = self.platform_bin_dir / f"magiskboot{exe_ext}"
        self.tools.aapt2 = self.platform_bin_dir / f"aapt2{exe_ext}"
        
        # >> Java tools
        self.tools.apktool_jar = self.bin_root / "apktool" / "apktool_2.12.1.jar" # Example
        self.tools.apkeditor_jar = self.bin_root / "apktool" / "APKEditor.jar"
        
        # Check critical tools
        if not self.tools.magiskboot.exists():
            self.logger.warning(f"magiskboot not found at {self.tools.magiskboot}")
            
    def initialize_target(self):
        """
        Initialize target workspace (Parallel optimized).
        1. Define partition sources (Stock vs Port).
        2. Extract and copy folders.
        3. Copy corresponding SELinux/fs_config configurations.
        """
        self.logger.info(f"Initializing Target Workspace at {self.target_dir}")

        # Clean old data (optional)
        if self.target_dir.exists():
            shutil.rmtree(self.target_dir)
            pass
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.target_config_dir.mkdir(parents=True, exist_ok=True)
        self.repack_images_dir.mkdir(parents=True, exist_ok=True)

        partition_layout = {
            # Low-level drivers -> From Stock
            'vendor': self.stock,
            'odm': self.stock,
            'vendor_dlkm': self.stock,
            'odm_dlkm': self.stock,
            'system_dlkm': self.stock,
            
            # System partitions -> From Port
            'system': self.port,
            'system_ext': self.port,
            'product': self.port,
            'mi_ext': self.port,
            'product_dlkm': self.port,

        }

        # Use ThreadPoolExecutor for parallel partition installation
        max_workers = 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for part_name, source_rom in partition_layout.items():
                futures.append(
                    executor.submit(self._install_partition, part_name, source_rom)
                )
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Partition install failed: {e}")
                    # raise e # Optional

        self._copy_firmware_images(list(partition_layout.keys())) 
        
        self.get_rom_info()        

        self.logger.info("Target Workspace Initialized.")

    def _install_partition(self, part_name: str, source_rom: RomPackage):
        """Install partition from source ROM to Target"""
        
        # 1. Extract source partition
        src_dir = source_rom.extract_partition_to_file(part_name)
        
        if not src_dir or not src_dir.exists():
            self.logger.warning(f"Partition {part_name} missing in {source_rom.label}, skipping.")
            return

        # 2. Copy partition files to target directory
        dest_dir = self.target_dir / f"{part_name}"
        
        if dest_dir.exists():
            shutil.rmtree(dest_dir) # Remove existing directory
        
        try:
            # Use cp -a for archive mode and --reflink=auto for CoW optimization
            cmd = ["cp", "-a", "--reflink=auto", str(src_dir), str(dest_dir)]
            self.shell.run(cmd)
            
        except Exception as e:
            # Fallback to shutil if cp fails
            self.logger.error(f"Native copy failed, falling back to shutil: {e}")
            try:
                shutil.copytree(src_dir, dest_dir, symlinks=True, dirs_exist_ok=True)
            except Exception as e2:
                self.logger.error(f"Copy failed for {part_name}: {e2}")

        # 3. Copy partition configuration files
        src_fs, src_fc = source_rom.get_config_files(part_name)
        
        if src_fs.exists():
            shutil.copy2(src_fs, self.target_config_dir / f"{part_name}_fs_config")
        else:
            self.logger.warning(f"Missing fs_config for {part_name} in {source_rom.label}")

        if src_fc.exists():
            shutil.copy2(src_fc, self.target_config_dir / f"{part_name}_file_contexts")
        else:
            self.logger.warning(f"Missing file_contexts for {part_name} in {source_rom.label}")

    def _copy_firmware_images(self, exclude_list: list[str]):
        """
        Copy firmware images from Base ROM that don't need modification.
        Iterate .img files in stock/images/, excluding logical partitions.
        """
        self.logger.info("Copying firmware images from Base ROM...")
        
        # Ensure stock images directory exists
        if not self.stock.images_dir.exists():
            self.logger.warning("Stock images directory not found! Firmware copy skipped.")
            return

        copied_count = 0
        for img_file in self.stock.images_dir.glob("*.img"):
            part_name = img_file.stem  # Get filename without extension (e.g. "xbl")
            
            # Skip handled partitions, accounting for A/B slots
            clean_name = part_name.replace("_a", "").replace("_b", "")
            
            if clean_name in exclude_list:
                continue
            
            # Remaining files are Firmware (xbl, tz, boot, dtbo, etc.)
            dest_path = self.repack_images_dir / img_file.name
            
            self.logger.debug(f"Copying firmware: {img_file.name}")
            shutil.copy2(img_file, dest_path)
            copied_count += 1
            
        self.logger.info(f"Copied {copied_count} firmware images to {self.repack_images_dir}")

    def get_rom_info(self):
        """
        Fetch detailed parameters for Stock and Port ROMs.
        """
        self.logger.info("Fetching ROM build props...")

        # 1. Get Android Version
        # Map: ro.system.build.version.release
        self.base_android_version = self.stock.get_prop("ro.system.build.version.release") or \
                                    self.stock.get_prop("ro.build.version.release") or "0"
        self.port_android_version = self.port.get_prop("ro.system.build.version.release") or \
                                    self.port.get_prop("ro.build.version.release") or "0"
        
        self.logger.info(f"Android Version: Stock=[{self.base_android_version}], Port=[{self.port_android_version}]")
        # 2. Get SDK Version
        # Base SDK
        self.base_android_sdk = self.stock.get_prop("ro.vendor.build.version.sdk") or \
                                self.stock.get_prop("ro.build.version.sdk") or "0"
        self.port_android_sdk = self.port.get_prop("ro.system.build.version.sdk") or \
                                self.port.get_prop("ro.build.version.sdk") or "0"
        
        self.logger.info(f"SDK Version: Stock=[{self.base_android_sdk}], Port=[{self.port_android_sdk}]")

        # 3. Calculate ROM Version and Codename Replacement
        # Base Incremental (Vendor)
        stock_rom_version_inc = self.stock.get_prop("ro.vendor.build.version.incremental", "")
        # Port HyperOS Version (mi_ext)
        port_mios_version_inc = self.port.get_prop("ro.mi.os.version.incremental") or \
                                self.port.get_prop("ro.build.version.incremental", "")

        # Port device codename logic (e.g. UNBCNXM -> U)
        try:
            port_parts = port_mios_version_inc.split(".")
            if len(port_parts) >= 5:
                port_device_code_segment = port_parts[4] # e.g. UNBCNXM
            else:
                port_device_code_segment = "UNKNOWN"
        except:
            port_device_code_segment = "UNKNOWN"

        # Calculate target prefix (U/V/W)
        target_prefix = "U" # Default 14
        if self.port_android_version == "15": target_prefix = "V"
        elif self.port_android_version == "16": target_prefix = "W"

        # Construct new Base Device Code Segment
        # Logic: Take 5th segment of stock_rom_version_inc, remove first char, add new prefix
        # e.g. Base: 1.0.5.0.UMCCNXM -> MCC -> V + MCC = VMCC (if A15)
        new_base_code_segment = port_device_code_segment # Default: no change
        
        if stock_rom_version_inc:
            try:
                base_parts = stock_rom_version_inc.split(".")
                if len(base_parts) >= 5:
                    base_segment_raw = base_parts[4] # UMCCNXM
                    # cut -c 2- (remove first char)
                    suffix = base_segment_raw[1:]
                    new_base_code_segment = f"{target_prefix}{suffix}"
            except:
                pass

        # Generate final version
        if "DEV" in port_mios_version_inc:
            self.logger.warning("Dev ROM detected, skipping codename replacement.")
            self.target_rom_version = port_mios_version_inc
        else:
            if port_device_code_segment != "UNKNOWN":
                self.target_rom_version = port_mios_version_inc.replace(port_device_code_segment, new_base_code_segment)
            else:
                self.target_rom_version = port_mios_version_inc

        self.logger.info(f"ROM Version: Stock=[{stock_rom_version_inc}], Target=[{self.target_rom_version}]")

        # 4. Get Device Code
        # Base: Scan product/etc/device_features/*.xml
        try:
            base_feat_dir = self.stock.extracted_dir / "product/etc/device_features"
            # Get first xml file name
            xml_file = next(base_feat_dir.glob("*.xml"))
            self.stock_rom_code = xml_file.stem # Filename without extension
        except StopIteration:
            # Fallback to prop if xml not found
            self.stock_rom_code = self.stock.get_prop("ro.product.vendor.device") or "unknown"
        except Exception as e:
            self.logger.warning(f"Error detecting base rom code: {e}")
            self.stock_rom_code = "unknown"

        # Port: ro.product.product.name
        self.port_rom_code = self.port.get_prop("ro.product.product.name") or "unknown"
        
        self.logger.info(f"Device Code: Stock=[{self.stock_rom_code}], Port=[{self.port_rom_code}]")

        # 5. AB Partition Check
        # Check Stock vendor for AB property
        ab_prop = self.stock.get_prop("ro.build.ab_update")
        if ab_prop and ab_prop.lower() == "true":
            self.is_ab_device = True
        else:
            self.is_ab_device = False
        
        self.logger.info(f"Is AB Device: {self.is_ab_device}")
    

    def get_target_prop_file(self, part_name):
        """
        Find build.prop in build/target/{part_name}
        """
        part_dir = self.target_dir / part_name
        if not part_dir.exists():
            return None
            
        # Check root
        p1 = part_dir / "build.prop"
        if p1.exists(): return p1
        
        # Check system/build.prop (for system partition)
        p2 = part_dir / "system" / "build.prop"
        if p2.exists(): return p2
        
        # Check etc/build.prop (for product/odm etc)
        p3 = part_dir / "etc" / "build.prop"
        if p3.exists(): return p3
        
        # Recursive fallback (slow)
        try:
            return next(part_dir.rglob("build.prop"))
        except StopIteration:
            return None
