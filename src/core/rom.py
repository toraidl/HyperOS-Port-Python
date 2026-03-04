import logging
import shutil
import zipfile
import tarfile
import concurrent.futures
import os
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Dict, Union, Tuple
from src.utils.shell import ShellRunner

ANDROID_LOGICAL_PARTITIONS: List[str] = [
    "system",
    "system_ext",
    "product",
    "vendor",
    "odm",
    "mi_ext",
    "system_dlkm",
    "vendor_dlkm",
    "odm_dlkm",
    "product_dlkm",
]


class RomType(Enum):
    UNKNOWN = auto()
    PAYLOAD = auto()  # payload.bin
    BROTLI = auto()  # new.dat.br
    FASTBOOT = auto()  # super.img or tgz
    LOCAL_DIR = auto()  # Pre-extracted directory


class RomPackage:
    def __init__(
        self,
        file_path: Union[str, Path],
        work_dir: Union[str, Path],
        label: str = "Rom",
    ):
        self.props: Dict[str, str] = {}
        self.prop_history: Dict[
            str, List[Tuple[str, str]]
        ] = {}  # Tracks property history: {key: [(file, value), ...]}
        self.path: Path = Path(file_path).resolve()
        self.work_dir: Path = Path(work_dir).resolve()
        self.label: str = label
        self.logger: logging.Logger = logging.getLogger(label)
        self.shell: ShellRunner = ShellRunner()

        # Directory structure definition
        self.images_dir: Path = self.work_dir / "images"  # Stores .img files
        self.extracted_dir: Path = (
            self.work_dir / "extracted"
        )  # Stores extracted folders (system, vendor...)
        self.config_dir: Path = (
            self.work_dir / "extracted" / "config"
        )  # Stores fs_config and file_contexts

        self.rom_type: RomType = RomType.UNKNOWN
        self._detect_type()

    def _detect_type(self) -> None:
        """Detects ROM type (Zip, Payload, or Local Directory)"""
        if not self.path.exists():
            raise FileNotFoundError(f"Path not found: {self.path}")

        if self.path.is_dir():
            self.rom_type = RomType.LOCAL_DIR
            self.logger.info(f"[{self.label}] Source is a local directory.")
            # If in directory mode, assume it's the working directory
            self.work_dir = self.path
            self.images_dir = self.path / "images"  # Adapting to AOSP structure
            if not self.images_dir.exists():
                self.images_dir = self.path  # Compatible if img is in root
            return

        # Simple Zip detection logic
        if zipfile.is_zipfile(self.path):
            with zipfile.ZipFile(self.path, "r") as z:
                namelist = z.namelist()
                if "payload.bin" in namelist:
                    self.rom_type = RomType.PAYLOAD
                elif any(x.endswith("new.dat.br") for x in namelist):
                    self.rom_type = RomType.BROTLI
                elif "images/super.img" in namelist or "super.img" in namelist:
                    self.rom_type = RomType.FASTBOOT
        elif self.path.suffix == ".tgz":
            self.rom_type = RomType.FASTBOOT

        self.logger.info(f"[{self.label}] Detected Type: {self.rom_type.name}")

    def extract_images(self, partitions: Optional[List[str]] = None) -> None:
        """
        Level 1 Extraction: Convert Zip/Payload to Img
        :param partitions:
            - If None (Base ROM): Extract ALL imgs from payload.bin (including firmware),
              but only automatically extract (Level 2) ANDROID_LOGICAL_PARTITIONS.
            - If list specified (Port ROM): Extract only specific imgs, and extract them.
        """
        if self.rom_type == RomType.LOCAL_DIR:
            self.logger.info(
                f"[{self.label}] Local dir mode, skipping payload extraction."
            )
            # Local mode, try extracting logical partitions
            self._batch_extract_files(partitions or ANDROID_LOGICAL_PARTITIONS)
            return

        self.images_dir.mkdir(parents=True, exist_ok=True)

        # === Check if source has changed and should extract new images ===
        # Compute hash of source file for change detection
        source_hash_path = self.work_dir / "source_file.hash"

        # Compare with previously stored hash
        source_changed = True  # Assume change by default
        current_source_hash = self._compute_file_hash(self.path)

        if source_hash_path.exists():
            try:
                with open(source_hash_path, "r") as f:
                    saved_hash = f.read().strip()
                source_changed = saved_hash != current_source_hash
            except Exception:
                self.logger.warning(
                    f"[{self.label}] Could not read hash file, re-extracting."
                )
                source_changed = True  # Error reading hash file, consider as changed
        else:
            source_changed = True  # No hash file exists, assume source change

        if source_changed:
            self.logger.info(
                f"[{self.label}] Source file changed, starting re-extraction..."
            )
        else:
            self.logger.info(
                f"[{self.label}] Source file unchanged, checking cached data..."
            )

            # Check if cached images exist and are not empty
            if any(self.images_dir.iterdir()):
                self.logger.info(
                    f"[{self.label}] Using cached images from previous extraction."
                )
                self._batch_extract_files(partitions or ANDROID_LOGICAL_PARTITIONS)
                return  # Nothing to do if cached extraction is valid
            else:
                self.logger.info(
                    f"[{self.label}] Source unchanged but images missing, re-extracting..."
                )
                source_changed = True

        # Execute extraction if source changed
        try:
            if self.rom_type == RomType.PAYLOAD:
                cmd = ["payload-dumper", "--out", str(self.images_dir)]

                if partitions:
                    # Port ROM mode: Extract specific images (e.g., system, product)
                    self.logger.info(
                        f"[{self.label}] Extracting specific images: {partitions} ..."
                    )
                    cmd.extend(["--partitions", ",".join(partitions)])
                else:
                    # Base ROM mode: Extract all images (includes firmware like xbl, boot)
                    self.logger.info(
                        f"[{self.label}] Extracting ALL images (Firmware + Logical) ..."
                    )

                cmd.append(str(self.path))
                self.shell.run(cmd)

            elif self.rom_type == RomType.BROTLI:
                # 1. Extract zip content
                with zipfile.ZipFile(self.path, "r") as z:
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
                            part_name = Path(f).name.split(".")[0]
                            if not partitions or part_name in partitions:
                                should_extract = True

                        if should_extract:
                            self.logger.info(f"Extracting {f}...")
                            z.extract(f, self.images_dir)

                # 2. Process .br files
                for br_file in self.images_dir.glob("*.new.dat.br"):
                    prefix = br_file.name.replace(".new.dat.br", "")

                    new_dat = self.images_dir / f"{prefix}.new.dat"
                    transfer_list = self.images_dir / f"{prefix}.transfer.list"
                    output_img = self.images_dir / f"{prefix}.img"

                    if output_img.exists():
                        self.logger.info(
                            f"[{self.label}] Image {output_img.name} already exists."
                        )
                        continue

                    if not transfer_list.exists():
                        self.logger.warning(
                            f"Transfer list for {prefix} not found, skipping conversion."
                        )
                        continue

                    # 3. Brotli Decompress
                    self.logger.info(f"[{self.label}] Decompressing {br_file.name}...")
                    try:
                        cmd = ["brotli", "-d", "-f", str(br_file), "-o", str(new_dat)]
                        self.shell.run(cmd)
                    except Exception as e:
                        self.logger.error(
                            f"Brotli decompression failed for {prefix}: {e}"
                        )
                        continue

                    # 4. sdat2img
                    self.logger.info(
                        f"[{self.label}] Converting {prefix} to raw image..."
                    )
                    try:
                        from src.utils.sdat2img import run_sdat2img

                        success = run_sdat2img(
                            str(transfer_list), str(new_dat), str(output_img)
                        )

                        if not success:
                            self.logger.error(f"sdat2img failed for {prefix}")
                        else:
                            self.logger.info(
                                f"[{self.label}] Generated {output_img.name}"
                            )
                            if new_dat.exists():
                                os.remove(new_dat)
                            if br_file.exists():
                                os.remove(br_file)
                            if transfer_list.exists():
                                os.remove(transfer_list)

                    except Exception as e:
                        self.logger.error(f"sdat2img execution failed: {e}")

            elif self.rom_type == RomType.FASTBOOT:
                # Zip mode logic
                with zipfile.ZipFile(self.path, "r") as z:
                    for f in z.namelist():
                        if f.endswith("super.img") or f.endswith("images/super.img"):
                            pass
                        elif not f.endswith(".img"):
                            continue

                        part_name = Path(f).stem
                        if partitions and part_name not in partitions:
                            continue

                        self.logger.info(f"Extracting {f}...")
                        source = z.open(f)
                        target = open(self.images_dir / Path(f).name, "wb")
                        with source, target:
                            shutil.copyfileobj(source, target)

                    self._process_sparse_images()

                    super_img = self.images_dir / "super.img"
                    if super_img.exists():
                        self.logger.info(
                            f"[{self.label}] Found super.img, unpacking logical partitions..."
                        )

                        try:
                            if partitions:
                                self.logger.info(
                                    f"[{self.label}] Unpacking specific partitions: {partitions}"
                                )
                                for part in partitions:
                                    cmd = [
                                        "lpunpack",
                                        "-p",
                                        part,
                                        str(super_img),
                                        str(self.images_dir),
                                    ]
                                    self.shell.run(cmd, check=False)
                                    cmd_a = [
                                        "lpunpack",
                                        "-p",
                                        f"{part}_a",
                                        str(super_img),
                                        str(self.images_dir),
                                    ]
                                    self.shell.run(cmd_a, check=False)
                            else:
                                self.logger.info(
                                    f"[{self.label}] Unpacking ALL partitions from super.img..."
                                )
                                self.shell.run(
                                    ["lpunpack", str(super_img), str(self.images_dir)]
                                )

                        except Exception as e:
                            self.logger.error(f"Failed to unpack super.img: {e}")
                            raise
                        finally:
                            if super_img.exists():
                                os.remove(super_img)

        except Exception as e:
            self.logger.error(f"Image extraction failed: {e}")
            raise

        self._batch_extract_files(partitions or ANDROID_LOGICAL_PARTITIONS)

        # After successful extraction, save the source hash to avoid reprocessing
        if source_changed:
            try:
                with open(source_hash_path, "w") as f:
                    f.write(current_source_hash)
                self.logger.info(
                    f"[{self.label}] Saved source file hash for future change detection."
                )
            except Exception as e:
                self.logger.warning(
                    f"[{self.label}] Could not save source hash file: {e}"
                )

    def _process_sparse_images(self) -> None:
        """Merge/Convert sparse images (super.img.*, cust.img.*) using simg2img"""
        candidate_paths = [
            Path("bin/linux/x86_64/simg2img").resolve(),
            Path("./simg2img"),
        ]

        simg2img_bin: Union[str, Path] = "simg2img"
        for path_candidate in candidate_paths:
            if path_candidate.exists():
                simg2img_bin = path_candidate
                break

        if isinstance(simg2img_bin, Path):
            self.logger.info(f"Using simg2img binary: {simg2img_bin}")
        else:
            self.logger.info("Using simg2img from system PATH")

        # 1. Handle super.img
        super_chunks = sorted(list(self.images_dir.glob("super.img.*")))
        target_super = self.images_dir / "super.img"

        if super_chunks:
            self.logger.info(
                f"[{self.label}] Merging sparse super images: {[c.name for c in super_chunks]}..."
            )
            try:
                cmd = (
                    [str(simg2img_bin)]
                    + [str(c) for c in super_chunks]
                    + [str(target_super)]
                )
                self.shell.run(cmd)
                for c in super_chunks:
                    os.unlink(c)
            except Exception as e:
                self.logger.error(f"Failed to merge super.img: {e}")
                raise

        elif target_super.exists():
            self.logger.info(
                f"[{self.label}] converting super.img to raw (if sparse)..."
            )
            temp_raw = self.images_dir / "super.raw.img"
            try:
                self.shell.run([str(simg2img_bin), str(target_super), str(temp_raw)])
                shutil.move(temp_raw, target_super)
            except Exception as e:
                self.logger.warning(f"simg2img conversion skipped/failed: {e}")
                if temp_raw.exists():
                    os.unlink(temp_raw)

        # 2. Handle cust.img
        cust_chunks = sorted(list(self.images_dir.glob("cust.img.*")))
        target_cust = self.images_dir / "cust.img"

        if cust_chunks:
            self.logger.info(f"[{self.label}] Merging sparse cust images...")
            try:
                cmd = (
                    [str(simg2img_bin)]
                    + [str(c) for c in cust_chunks]
                    + [str(target_cust)]
                )
                self.shell.run(cmd)
                for c in cust_chunks:
                    os.unlink(c)
            except Exception as e:
                self.logger.error(f"Failed to merge cust.img: {e}")

    def _batch_extract_files(self, candidates: List[str]) -> None:
        """Batch call extract_partition_to_file (Parallel optimization)"""
        self.logger.info(
            f"[{self.label}] Processing file extraction for logical partitions..."
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for part in candidates:
                img_path = self.images_dir / f"{part}.img"
                if not img_path.exists():
                    img_path = self.images_dir / f"{part}_a.img"

                if img_path.exists():
                    futures.append(
                        executor.submit(self.extract_partition_to_file, part)
                    )
                else:
                    self.logger.debug(
                        f"[{self.label}] Partition image {part} not found, skipping extract."
                    )

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Partition extraction failed: {e}")
                    raise

    def extract_partition_to_file(self, part_name: str) -> Optional[Path]:
        """Level 2 Extraction: Extract Img to folder, preserving SELinux config"""
        target_dir = self.extracted_dir / part_name
        config_exists = (self.config_dir / f"{part_name}_fs_config").exists()
        has_content = target_dir.exists() and any(target_dir.iterdir())

        if has_content and config_exists:
            self.logger.info(f"[{self.label}] Partition {part_name} already extracted.")
            return target_dir

        img_path = self.images_dir / f"{part_name}.img"
        if not img_path.exists():
            img_path = self.images_dir / f"{part_name}_a.img"
            if not img_path.exists():
                self.logger.warning(f"[{self.label}] Image {part_name}.img not found.")
                return None

        self.logger.info(f"[{self.label}] Extracting {part_name}.img to filesystem...")
        target_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        try:
            cmd = [
                "extract.erofs",
                "-x",
                "-i",
                str(img_path),
                "-o",
                str(self.extracted_dir),
            ]
            self.shell.run(cmd, capture_output=True)
        except Exception as e:
            self.logger.error(f"Failed to extract {part_name}: {e}")
            return None

        possible_contexts = list(
            target_dir.parent.glob(f"{part_name}*_file_contexts")
        ) + list(target_dir.glob("*_file_contexts"))
        possible_fs_config = list(
            target_dir.parent.glob(f"{part_name}*_fs_config")
        ) + list(target_dir.glob("*_fs_config"))

        if possible_contexts:
            shutil.move(
                possible_contexts[0], self.config_dir / f"{part_name}_file_contexts"
            )
        if possible_fs_config:
            shutil.move(
                possible_fs_config[0], self.config_dir / f"{part_name}_fs_config"
            )

        return target_dir

    def get_config_files(self, part_name: str) -> Tuple[Path, Path]:
        """Get config file paths for a partition"""
        return (
            self.config_dir / f"{part_name}_fs_config",
            self.config_dir / f"{part_name}_file_contexts",
        )

    def parse_all_props(self) -> None:
        """Scan and parse all build.prop files in extracted dir"""
        if not self.extracted_dir.exists():
            self.logger.warning(
                f"[{self.label}] Extracted dir not found, skipping props parsing."
            )
            return

        self.props = {}
        self.prop_history = {}
        self.logger.info(f"[{self.label}] Scanning and parsing all build.prop files...")

        prop_files = list(self.extracted_dir.rglob("build.prop"))
        if not prop_files:
            self.logger.warning(f"[{self.label}] No build.prop files found.")
            return

        def sort_priority(path: Path) -> int:
            p = str(path).lower()
            if "system" in p:
                return 0
            if "vendor" in p:
                return 1
            if "product" in p:
                return 2
            if "odm" in p:
                return 3
            if "mi_ext" in p:
                return 4
            return 99

        prop_files.sort(key=sort_priority)
        for prop_file in prop_files:
            self._load_single_prop_file(prop_file)

        self.logger.info(
            f"[{self.label}] Loaded {len(self.props)} properties from {len(prop_files)} files."
        )

    def _load_single_prop_file(self, file_path: Path) -> None:
        """Helper: Parse single file and update self.props"""
        try:
            rel_path = file_path.relative_to(self.extracted_dir)
        except ValueError:
            rel_path = file_path.name

        self.logger.debug(f"Parsing: {rel_path}")
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key, value = key.strip(), value.strip()
                    if key not in self.prop_history:
                        self.prop_history[key] = []
                    self.prop_history[key].append((str(rel_path), value))
                    self.props[key] = value
        except Exception as e:
            self.logger.error(f"Error reading {rel_path}: {e}")

    def export_props(self, output_path: Union[str, Path]) -> None:
        """Export all props to file, including Override debug info"""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"[{self.label}] Exporting debug props to {out_file} ...")

        if not self.props:
            self.parse_all_props()

        content = [
            f"# DEBUG DUMP for {self.label}",
            "# Generated by HyperOS Porting Tool",
            "# ==========================================\n",
        ]
        for key in sorted(self.props.keys()):
            history = self.prop_history.get(key, [])
            final_val = self.props[key]
            if len(history) > 1:
                content.append(f"# [OVERRIDE DETECTED]\n# {key}")
                for source, val in history:
                    content.append(f"#   - {source}: {val}")
                content.append(f"#   -> Final: {final_val}")
            content.append(f"{key}={final_val}")

        with open(out_file, "w", encoding="utf-8") as f:
            f.write("\n".join(content))
        self.logger.info(f"[{self.label}] Debug props saved.")

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of a file for change detection."""
        import hashlib

        hash_sha256 = hashlib.sha256()

        with open(file_path, "rb") as f:
            # Read file in chunks to avoid memory issues with large files
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)

        # Return first 16 characters of the hash (similar to how git handles it)
        return hash_sha256.hexdigest()[:16]

    def get_prop(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get property value. Triggers full load if cache is empty."""
        if not self.props:
            self.parse_all_props()
        return self.props.get(key, default)
