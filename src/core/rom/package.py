from __future__ import annotations

import concurrent.futures
import logging
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

from src.utils.shell import ShellRunner

from .constants import ANDROID_LOGICAL_PARTITIONS, RomType
from .extractors import extract_local
from .utils import compute_file_hash, load_single_prop_file, sort_prop_priority

if TYPE_CHECKING:
    pass


class RomPackage:
    """Represents a ROM package and provides extraction/processing methods."""

    def __init__(
        self,
        file_path: Union[str, Path],
        work_dir: Union[str, Path],
        label: str = "Rom",
    ):
        """Initialize RomPackage.

        Args:
            file_path: Path to the ROM file or directory.
            work_dir: Working directory for extraction.
            label: Label for logging purposes.
        """
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
        """Detects ROM type (Zip, Payload, or Local Directory)."""
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
        Level 1 Extraction: Convert Zip/Payload to Img.

        Args:
            partitions:
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

        # === Check if source has changed and should extract new images ===
        # Compute hash of source file for change detection
        source_hash_path = self.work_dir / "source_file.hash"

        # Compare with previously stored hash
        source_changed = True  # Assume change by default
        current_source_hash = compute_file_hash(self.path)

        if source_hash_path.exists():
            try:
                with open(source_hash_path, "r") as f:
                    saved_hash = f.read().strip()
                source_changed = saved_hash != current_source_hash
            except Exception:
                self.logger.warning(f"[{self.label}] Could not read hash file, re-extracting.")
                source_changed = True  # Error reading hash file, consider as changed
        else:
            source_changed = True  # No hash file exists, assume source change

        if source_changed:
            self.logger.info(f"[{self.label}] Source file changed, starting re-extraction...")
            # Clean up old extracted data to avoid stale cache
            if self.extracted_dir.exists():
                self.logger.info(f"[{self.label}] Cleaning up old extracted directory...")
                shutil.rmtree(self.extracted_dir)
            if self.config_dir.exists():
                shutil.rmtree(self.config_dir)
            # Clean up old images as well for consistency
            if any(self.images_dir.iterdir()):
                self.logger.info(f"[{self.label}] Cleaning up old images directory...")
                for item in self.images_dir.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
        else:
            self.logger.info(f"[{self.label}] Source file unchanged, checking cached data...")

            # Check if cached images exist and are not empty
            if any(self.images_dir.iterdir()):
                self.logger.info(f"[{self.label}] Using cached images from previous extraction.")
                self._batch_extract_files(partitions or ANDROID_LOGICAL_PARTITIONS)
                return  # Nothing to do if cached extraction is valid
            else:
                self.logger.info(
                    f"[{self.label}] Source unchanged but images missing, re-extracting..."
                )
                source_changed = True

        # Execute extraction if source changed
        from .extractors import extract_brotli, extract_fastboot, extract_payload

        try:
            if self.rom_type == RomType.PAYLOAD:
                extract_payload(self, partitions)
            elif self.rom_type == RomType.BROTLI:
                extract_brotli(self, partitions)
            elif self.rom_type == RomType.FASTBOOT:
                extract_fastboot(self, partitions)

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
                self.logger.warning(f"[{self.label}] Could not save source hash file: {e}")

    def _batch_extract_files(self, candidates: List[str]) -> None:
        """Batch call extract_partition_to_file (Parallel optimization).

        Args:
            candidates: List of partition names to extract.
        """
        self.logger.info(f"[{self.label}] Processing file extraction for logical partitions...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for part in candidates:
                img_path = self.images_dir / f"{part}.img"
                if not img_path.exists():
                    img_path = self.images_dir / f"{part}_a.img"

                if img_path.exists():
                    futures.append(executor.submit(self.extract_partition_to_file, part))
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
        """Level 2 Extraction: Extract Img to folder, preserving SELinux config.

        Args:
            part_name: Name of the partition to extract.

        Returns:
            Path to the extracted directory, or None if extraction failed.
        """
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

        possible_contexts = list(target_dir.parent.glob(f"{part_name}*_file_contexts")) + list(
            target_dir.glob("*_file_contexts")
        )
        possible_fs_config = list(target_dir.parent.glob(f"{part_name}*_fs_config")) + list(
            target_dir.glob("*_fs_config")
        )

        if possible_contexts:
            shutil.move(possible_contexts[0], self.config_dir / f"{part_name}_file_contexts")
        if possible_fs_config:
            shutil.move(possible_fs_config[0], self.config_dir / f"{part_name}_fs_config")

        return target_dir

    def get_config_files(self, part_name: str) -> Tuple[Path, Path]:
        """Get config file paths for a partition.

        Args:
            part_name: Name of the partition.

        Returns:
            Tuple of (fs_config_path, file_contexts_path).
        """
        return (
            self.config_dir / f"{part_name}_fs_config",
            self.config_dir / f"{part_name}_file_contexts",
        )

    def parse_all_props(self) -> None:
        """Scan and parse all build.prop files in extracted dir."""
        if not self.extracted_dir.exists():
            self.logger.warning(f"[{self.label}] Extracted dir not found, skipping props parsing.")
            return

        self.props = {}
        self.prop_history = {}
        self.logger.info(f"[{self.label}] Scanning and parsing all build.prop files...")

        prop_files = list(self.extracted_dir.rglob("build.prop"))
        if not prop_files:
            self.logger.warning(f"[{self.label}] No build.prop files found.")
            return

        prop_files.sort(key=sort_prop_priority)
        for prop_file in prop_files:
            load_single_prop_file(
                prop_file, self.extracted_dir, self.props, self.prop_history, self.logger
            )

        self.logger.info(
            f"[{self.label}] Loaded {len(self.props)} properties from {len(prop_files)} files."
        )

    def export_props(self, output_path: Union[str, Path]) -> None:
        """Export all props to file, including Override debug info.

        Args:
            output_path: Path to write the exported properties.
        """
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

    def get_prop(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get property value. Triggers full load if cache is empty.

        Args:
            key: Property key to look up.
            default: Default value if key not found.

        Returns:
            Property value or default.
        """
        if not self.props:
            self.parse_all_props()
        return self.props.get(key, default)
