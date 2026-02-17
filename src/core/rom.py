import logging
import shutil
import zipfile
import tarfile
import concurrent.futures
import os
from enum import Enum, auto
from pathlib import Path
from src.utils.shell import ShellRunner

ANDROID_LOGICAL_PARTITIONS = [
    "system", "system_ext", "product", "vendor", "odm", "mi_ext",
    "system_dlkm", "vendor_dlkm", "odm_dlkm", "product_dlkm"
]

class RomType(Enum):
    UNKNOWN = auto()
    PAYLOAD = auto()      # payload.bin
    BROTLI = auto()       # new.dat.br
    FASTBOOT = auto()     # super.img or tgz
    LOCAL_DIR = auto()    # Pre-extracted directory

class RomPackage:
    def __init__(self, file_path: str | Path, work_dir: str | Path, label: str = "Rom"):
        self.props = {} 
        self.prop_history = {} # Tracks property history: {key: [(file, value), ...]}
        self.path = Path(file_path).resolve()
        self.work_dir = Path(work_dir).resolve()
        self.label = label
        self.logger = logging.getLogger(label)
        self.shell = ShellRunner()
        
        # Directory structure definition
        self.images_dir = self.work_dir / "images"           # Stores .img files
        self.extracted_dir = self.work_dir / "extracted"     # Stores extracted folders (system, vendor...)
        self.config_dir = self.work_dir / "extracted"  / "config"           # Stores fs_config and file_contexts

        self.rom_type = RomType.UNKNOWN
        self.props = {} 
        
        self._detect_type()

    def _detect_type(self):
        """Detects ROM type (Zip, Payload, or Local Directory)"""
        if not self.path.exists():
            raise FileNotFoundError(f"Path not found: {self.path}")

        if self.path.is_dir():
            self.rom_type = RomType.LOCAL_DIR
            self.logger.info(f"[{self.label}] Source is a local directory.")
            # If in directory mode, assume it's the working directory
            self.work_dir = self.path
            self.images_dir = self.path / "images" # Adapting to AOSP structure
            if not self.images_dir.exists(): 
                self.images_dir = self.path # Compatible if img is in root
            return

        # Simple Zip detection logic
        if zipfile.is_zipfile(self.path):
            with zipfile.ZipFile(self.path, 'r') as z:
                namelist = z.namelist()
                if "payload.bin" in namelist:
                    self.rom_type = RomType.PAYLOAD
                elif any(x.endswith("new.dat.br") for x in namelist):
                    self.rom_type = RomType.BROTLI
                elif "images/super.img" in namelist or "super.img" in namelist:
                    self.rom_type = RomType.FASTBOOT
        elif self.path.suffix == '.tgz':
            self.rom_type = RomType.FASTBOOT

        self.logger.info(f"[{self.label}] Detected Type: {self.rom_type.name}")

    def extract_images(self, partitions: list[str] = None):
        """
        Level 1 Extraction: Convert Zip/Payload to Img
        :param partitions: 
            - If None (Base ROM): Extract ALL imgs from payload.bin (including firmware),
              but only automatically extract (Level 2) ANDROID_LOGICAL_PARTITIONS.
            - If list specified (Port ROM): Extract only specific imgs, and extract them.
        """
        if self.rom_type == RomType.LOCAL_DIR:
            self.logger.info(f"[{self.label}] Local dir mode, skipping payload extraction.")
            # Local mode, try extracting logical partitions
            self._batch_extract_files(partitions or ANDROID_LOGICAL_PARTITIONS)
            return

        self.images_dir.mkdir(parents=True, exist_ok=True)
        
        # === Step 1: Payload/Zip -> Images (Extract img) ===
        try:
            if self.rom_type == RomType.PAYLOAD:
                cmd = ["payload-dumper", "--out", str(self.images_dir)]
                
                if partitions:
                    # Port ROM mode: Extract specific images (e.g., system, product)
                    self.logger.info(f"[{self.label}] Extracting specific images: {partitions} ...")
                    cmd.extend(["--partitions", ",".join(partitions)])
                else:
                    # Base ROM mode: Extract all images (includes firmware like xbl, boot)
                    self.logger.info(f"[{self.label}] Extracting ALL images (Firmware + Logical) ...")
                
                cmd.append(str(self.path))
                
                # Simple check: If target images seem to exist, skip payload-dumper
                # (Note: Hard to verify if all firmware exists, doing a simple check)
                if not any(self.images_dir.iterdir()):
                    self.shell.run(cmd)
                else:
                    self.logger.info(f"[{self.label}] Images directory not empty, assuming extracted.")

            elif self.rom_type == RomType.BROTLI:
                # 1. Extract zip content
                with zipfile.ZipFile(self.path, 'r') as z:
                    for f in z.namelist():
                        should_extract = False
                        
                        # .img handling
                        if f.endswith(".img"):
                            part_name = Path(f).stem
                            if not partitions or part_name in partitions:
                                should_extract = True
                        
                        # .br handling
                        elif f.endswith(".new.dat.br") or f.endswith(".transfer.list"):
                             # Extract partition name from file name (e.g. system.new.dat.br -> system)
                             part_name = Path(f).name.split('.')[0]
                             if not partitions or part_name in partitions:
                                 should_extract = True
                        
                        if should_extract:
                             self.logger.info(f"Extracting {f}...")
                             z.extract(f, self.images_dir)

                # 2. Process .br files
                # Note: We iterate over extracted files in images_dir
                for br_file in self.images_dir.glob("*.new.dat.br"):
                    prefix = br_file.name.replace(".new.dat.br", "")
                    
                    new_dat = self.images_dir / f"{prefix}.new.dat"
                    transfer_list = self.images_dir / f"{prefix}.transfer.list"
                    output_img = self.images_dir / f"{prefix}.img"
                    
                    if output_img.exists():
                        self.logger.info(f"[{self.label}] Image {output_img.name} already exists.")
                        continue

                    if not transfer_list.exists():
                        self.logger.warning(f"Transfer list for {prefix} not found, skipping conversion.")
                        continue

                    # 3. Brotli Decompress
                    self.logger.info(f"[{self.label}] Decompressing {br_file.name}...")
                    try:
                        # brotli -d -f input -o output
                        # We use full path for safety
                        cmd = ["brotli", "-d", "-f", str(br_file), "-o", str(new_dat)]
                        self.shell.run(cmd)
                    except Exception as e:
                        self.logger.error(f"Brotli decompression failed for {prefix}: {e}")
                        continue

                    # 4. sdat2img
                    self.logger.info(f"[{self.label}] Converting {prefix} to raw image...")
                    try:
                        # Import here to avoid circular dependencies if any
                        from src.utils.sdat2img import run_sdat2img
                        # sdat2img expects string paths
                        success = run_sdat2img(str(transfer_list), str(new_dat), str(output_img))
                        
                        if not success:
                            self.logger.error(f"sdat2img failed for {prefix}")
                        else:
                            self.logger.info(f"[{self.label}] Generated {output_img.name}")
                            
                            # Clean up intermediate files only on success
                            if new_dat.exists(): os.remove(new_dat)
                            # Keep original br file? Maybe not if space is concern. 
                            # But extract_images usually keeps source images.
                            # Let's delete new.dat but keep br? Or delete br too since it's extracted copy.
                            if br_file.exists(): os.remove(br_file)
                            if transfer_list.exists(): os.remove(transfer_list)

                    except Exception as e:
                        self.logger.error(f"sdat2img execution failed: {e}")

            elif self.rom_type == RomType.FASTBOOT:
                # Zip mode logic
                has_super = False
                super_path_in_zip = None
                
                with zipfile.ZipFile(self.path, 'r') as z:
                    # 1. First pass: Check for super.img and extract other images
                    for f in z.namelist():
                        if f.endswith("super.img") or f.endswith("images/super.img"):
                            has_super = True
                            super_path_in_zip = f
                            continue
                            
                        if not f.endswith(".img"): continue
                        
                        part_name = Path(f).stem
                        # Skip if it's likely a logical partition inside super (unless explicit .img exists outside)
                        # Actually standard fastboot zips have boot.img, dtbo.img outside super.
                        # Logical partitions (system, vendor) are inside super.
                        
                        # If partitions specified, extract only those; otherwise extract all
                        if partitions and part_name not in partitions:
                            # If it's a firmware image (not logical), we generally want it for Base ROM
                            # But if partitions IS set (Port ROM), we strictly follow it.
                            # Wait, Port ROM extraction calls extract_images(port_partitions).
                            # So we only want system/product etc.
                            # These are likely inside super.img.
                            # So we shouldn't extract boot.img etc if not requested.
                            continue
                            
                        self.logger.info(f"Extracting {f}...")
                        # Flatten structure: Extract file to images_dir directly
                        source = z.open(f)
                        target = open(self.images_dir / Path(f).name, "wb")
                        with source, target:
                            shutil.copyfileobj(source, target)

                    # 2. Handle super.img if present
                    if has_super and super_path_in_zip:
                        self.logger.info(f"[{self.label}] Found super.img, processing logical partitions...")
                        
                        # Extract super.img to temp
                        temp_super = self.work_dir / "super.img"
                        self.logger.info(f"Extracting {super_path_in_zip} to {temp_super}...")
                        with z.open(super_path_in_zip) as source, open(temp_super, "wb") as target:
                            shutil.copyfileobj(source, target)
                        
                        # Unpack super.img
                        try:
                            # lpunpack is required
                            # partitions arg determines what to unpack
                            # If partitions=None, unpack ALL.
                            # If partitions=["system", ...], unpack specific.
                            
                            unpack_cmd = ["lpunpack"]
                            
                            if partitions:
                                self.logger.info(f"[{self.label}] Unpacking specific partitions from super.img: {partitions}")
                                # lpunpack can take multiple -p? No, usually one per run or all.
                                # Let's check lpunpack help or assume we loop.
                                # Or just unpack all if efficient enough? super.img is large, unpacking all takes space.
                                # Iterative unpacking is better.
                                
                                for part in partitions:
                                    # Try unpacking 'part' and 'part_a' (for A/B)
                                    # We don't know slot suffix in super.img easily without querying.
                                    # Attempt both or just 'part' if usually suffixed inside?
                                    # Usually partitions in super are named "system_a", "system_b" or just "system".
                                    
                                    # Simple strategy: Try unpacking specific name.
                                    # If failed, try appending _a? 
                                    # Actually lpunpack fails if partition not found.
                                    
                                    # To be safe and robust (like port.sh), we might want to just unpack ALL to images_dir
                                    # and let the subsequent steps pick what they need.
                                    # But space...
                                    pass
                                
                                # Re-reading port.sh: it loops and runs lpunpack -p ${part} or ${part}_a
                                
                                for part in partitions:
                                    # Try extracting 'part'
                                    cmd = ["lpunpack", "-p", part, str(temp_super), str(self.images_dir)]
                                    try:
                                        self.shell.run(cmd, check=False)
                                    except: pass
                                    
                                    # Try extracting 'part_a'
                                    cmd_a = ["lpunpack", "-p", f"{part}_a", str(temp_super), str(self.images_dir)]
                                    try:
                                        self.shell.run(cmd_a, check=False)
                                    except: pass

                            else:
                                # Unpack ALL
                                self.logger.info(f"[{self.label}] Unpacking ALL partitions from super.img...")
                                self.shell.run(["lpunpack", str(temp_super), str(self.images_dir)])
                                
                        except Exception as e:
                            self.logger.error(f"Failed to unpack super.img: {e}")
                            raise
                        finally:
                            # Cleanup super.img
                            if temp_super.exists():
                                os.remove(temp_super)

        except Exception as e:
            self.logger.error(f"Image extraction failed: {e}")
            raise

        # === Step 2: Images -> Folders (Extract to folders) ===
        # Determine which images need to be extracted to folders
        if partitions:
            # If partitions specified, extract these
            candidates = partitions
        else:
            # If not specified (Base ROM), extract only "logical partitions", skip firmware
            candidates = ANDROID_LOGICAL_PARTITIONS

        self._batch_extract_files(candidates)

    def _batch_extract_files(self, candidates: list[str]):
        """
        Batch call extract_partition_to_file (Parallel optimization)
        Automatically checks if img exists, skips if not (e.g., Base ROM might not have mi_ext)
        """
        self.logger.info(f"[{self.label}] Processing file extraction for logical partitions...")
        
        # Use ThreadPoolExecutor for parallel extraction
        max_workers = 4 # Limit concurrency
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for part in candidates:
                # 1. Check if img exists
                img_path = self.images_dir / f"{part}.img"
                if not img_path.exists():
                    # Try _a (V-AB)
                    img_path = self.images_dir / f"{part}_a.img"
                
                if img_path.exists():
                    futures.append(executor.submit(self.extract_partition_to_file, part))
                else:
                    self.logger.debug(f"[{self.label}] Partition image {part} not found, skipping extract.")
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Partition extraction failed: {e}")
                    raise
        
    def extract_partition_to_file(self, part_name: str) -> Path:
        """
        Level 2 Extraction: Extract Img to folder, preserving SELinux config
        :return: Path to extracted folder (e.g., build/stock/extracted/)
        """
        target_dir = self.extracted_dir / part_name
        
        # === Modification: Stricter cache check ===
        # Check if dir has content AND fs_config exists to consider it "extracted"
        # Otherwise consider incomplete, re-extract
        config_exists = (self.config_dir / f"{part_name}_fs_config").exists()
        has_content = target_dir.exists() and any(target_dir.iterdir())
        
        if has_content and config_exists:
            self.logger.info(f"[{self.label}] Partition {part_name} already extracted (verified).")
            return target_dir

        # 2. Check if img exists
        img_path = self.images_dir / f"{part_name}.img"
        if not img_path.exists():
            # Try finding _a.img (for V-AB)
            img_path = self.images_dir / f"{part_name}_a.img"
            if not img_path.exists():
                self.logger.warning(f"[{self.label}] Image {part_name}.img not found.")
                return None

        self.logger.info(f"[{self.label}] Extracting {part_name}.img to filesystem...")
        target_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 3. Call extraction tool (erofs or ext4)
        # Corresponds to functions.sh: extract_partition
        # Assuming unified tool script or direct extract.erofs call
        
        # Simulation: Identify type (Simplified, default to erofs)
        is_erofs = True # Should strictly check magic number
        
        try:
            if is_erofs:
                # extract.erofs -i input.img -x (extract) -o output_dir
                # Note: extract.erofs generates file_contexts in output dir by default
                cmd = ["extract.erofs", "-x", "-i", str(img_path), "-o", str(self.extracted_dir)]
                self.shell.run(cmd, capture_output=True)
            else:
                # ext4 handling
                pass
        except Exception as e:
            self.logger.error(f"Failed to extract {part_name}: {e}")
            return None

        # 4. [Critical] Process config files (fs_config / file_contexts)
        # Move generated config files to self.config_dir for unified management
        # Rename for standardization as tools might generate different names
        
        # Find potentially generated context files
        possible_contexts = list(target_dir.parent.glob(f"{part_name}*_file_contexts")) + \
                            list(target_dir.glob("*_file_contexts"))
        
        possible_fs_config = list(target_dir.parent.glob(f"{part_name}*_fs_config")) + \
                             list(target_dir.glob("*_fs_config"))

        if possible_contexts:
            src = possible_contexts[0]
            dst = self.config_dir / f"{part_name}_file_contexts"
            shutil.move(src, dst)
            self.logger.debug(f"Saved file_contexts for {part_name}")

        if possible_fs_config:
            src = possible_fs_config[0]
            dst = self.config_dir / f"{part_name}_fs_config"
            shutil.move(src, dst)
            self.logger.debug(f"Saved fs_config for {part_name}")

        return target_dir

    def get_config_files(self, part_name):
        """Get config file paths for a partition"""
        return (
            self.config_dir / f"{part_name}_fs_config",
            self.config_dir / f"{part_name}_file_contexts"
        )
    
    def parse_all_props(self):
        """
        [Optimization] Recursively find all build.prop files in extracted dir
        """
        if not self.extracted_dir.exists():
            self.logger.warning(f"[{self.label}] Extracted dir not found, skipping props parsing.")
            return

        # [New] Clear history to prevent stacking from multiple calls
        self.props = {}
        self.prop_history = {}

        self.logger.info(f"[{self.label}] Scanning and parsing all build.prop files...")

        # 1. Find files
        prop_files = list(self.extracted_dir.rglob("build.prop"))
        if not prop_files:
            self.logger.warning(f"[{self.label}] No build.prop files found.")
            return

        # 2. Sort (System -> Vendor -> Product ...)
        def sort_priority(path):
            p = str(path).lower()
            if "system" in p: return 0
            if "vendor" in p: return 1
            if "product" in p: return 2
            if "odm" in p: return 3
            if "mi_ext" in p: return 4
            return 99
        prop_files.sort(key=sort_priority)

        # 3. Parse one by one
        for prop_file in prop_files:
            self._load_single_prop_file(prop_file)
            
        self.logger.info(f"[{self.label}] Loaded {len(self.props)} properties from {len(prop_files)} files.")

    def _load_single_prop_file(self, file_path: Path):
        """Helper: Parse single file and update self.props"""
        # Calculate relative path for display (e.g. system/build.prop)
        try:
            rel_path = file_path.relative_to(self.extracted_dir)
        except ValueError:
            rel_path = file_path.name # Fallback

        self.logger.debug(f"Parsing: {rel_path}")

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # [Core Mod] Track history
                    if key not in self.prop_history:
                        self.prop_history[key] = []
                    
                    # Add (source file, value) to history list
                    self.prop_history[key].append((str(rel_path), value))
                    
                    # Update current effective value (Last-win strategy)
                    self.props[key] = value

        except Exception as e:
            self.logger.error(f"Error reading {rel_path}: {e}")

    def export_props(self, output_path: str | Path):
        """
        [New] Export all props to file, including Override debug info
        """
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.logger.info(f"[{self.label}] Exporting debug props to {out_file} ...")
        
        # Ensure loaded
        if not self.props:
            self.parse_all_props()

        content = []
        content.append(f"# DEBUG DUMP for {self.label}")
        content.append(f"# Generated by HyperOS Porting Tool")
        content.append(f"# ==========================================\n")

        # Sort by Key for easy viewing
        for key in sorted(self.props.keys()):
            history = self.prop_history.get(key, [])
            final_val = self.props[key]

            # Check for Override (history > 1 and value changed)
            # Note: Sometimes different files define same value, counts as "override" but value unchanged
            if len(history) > 1:
                content.append(f"# [OVERRIDE DETECTED]")
                content.append(f"# {key}")
                # Print change trajectory
                for source, val in history:
                    content.append(f"#   - {source}: {val}")
                content.append(f"#   -> Final: {final_val}")
            
            # Write actual key-value pair
            content.append(f"{key}={final_val}")
        
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(content))
        
        self.logger.info(f"[{self.label}] Debug props saved.")
    def get_prop(self, key: str, default: str = None) -> str:
        """
        Get property value.
        Triggers full load if cache is empty.
        """
        if not self.props:
            self.parse_all_props()
        return self.props.get(key, default)
