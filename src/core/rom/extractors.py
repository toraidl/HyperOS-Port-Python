from __future__ import annotations

import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .package import RomPackage


def extract_payload(
    package: RomPackage,
    partitions: Optional[List[str]],
) -> None:
    """Extract payload.bin from ROM package.

    Args:
        package: The RomPackage instance.
        partitions: List of partitions to extract (None = all).
    """
    cmd = ["payload-dumper", "--out", str(package.images_dir)]

    if partitions:
        package.logger.info(f"[{package.label}] Extracting specific images: {partitions} ...")
        cmd.extend(["--partitions", ",".join(partitions)])
    else:
        package.logger.info(f"[{package.label}] Extracting ALL images (Firmware + Logical) ...")

    cmd.append(str(package.path))
    package.shell.run(cmd)


def extract_brotli(
    package: RomPackage,
    partitions: Optional[List[str]],
) -> None:
    """Extract and convert brotli-compressed images from ROM package.

    Args:
        package: The RomPackage instance.
        partitions: List of partitions to extract (None = all).
    """
    # 1. Extract zip content
    with zipfile.ZipFile(package.path, "r") as z:
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
                package.logger.info(f"Extracting {f}...")
                z.extract(f, package.images_dir)

    # 2. Process .br files
    for br_file in package.images_dir.glob("*.new.dat.br"):
        prefix = br_file.name.replace(".new.dat.br", "")

        new_dat = package.images_dir / f"{prefix}.new.dat"
        transfer_list = package.images_dir / f"{prefix}.transfer.list"
        output_img = package.images_dir / f"{prefix}.img"

        if output_img.exists():
            package.logger.info(f"[{package.label}] Image {output_img.name} already exists.")
            continue

        if not transfer_list.exists():
            package.logger.warning(f"Transfer list for {prefix} not found, skipping conversion.")
            continue

        # 3. Brotli Decompress
        package.logger.info(f"[{package.label}] Decompressing {br_file.name}...")
        try:
            cmd = ["brotli", "-d", "-f", str(br_file), "-o", str(new_dat)]
            package.shell.run(cmd)
        except Exception as e:
            package.logger.error(f"Brotli decompression failed for {prefix}: {e}")
            continue

        # 4. sdat2img
        package.logger.info(f"[{package.label}] Converting {prefix} to raw image...")
        try:
            from src.utils.sdat2img import run_sdat2img

            success = run_sdat2img(str(transfer_list), str(new_dat), str(output_img))

            if not success:
                package.logger.error(f"sdat2img failed for {prefix}")
            else:
                package.logger.info(f"[{package.label}] Generated {output_img.name}")
                if new_dat.exists():
                    os.remove(new_dat)
                if br_file.exists():
                    os.remove(br_file)
                if transfer_list.exists():
                    os.remove(transfer_list)

        except Exception as e:
            package.logger.error(f"sdat2img execution failed: {e}")


def extract_fastboot(
    package: RomPackage,
    partitions: Optional[List[str]],
) -> None:
    """Extract fastboot images (super.img) from ROM package.

    Args:
        package: The RomPackage instance.
        partitions: List of partitions to extract (None = all).
    """
    # Zip mode logic
    with zipfile.ZipFile(package.path, "r") as z:
        for f in z.namelist():
            if f.endswith("super.img") or f.endswith("images/super.img"):
                pass
            elif not f.endswith(".img"):
                continue

            part_name = Path(f).stem
            if partitions and part_name not in partitions:
                continue

            package.logger.info(f"Extracting {f}...")
            source = z.open(f)
            target = open(package.images_dir / Path(f).name, "wb")
            with source, target:
                shutil.copyfileobj(source, target)

        from .utils import process_sparse_images

        process_sparse_images(package.images_dir, package.logger, package.shell)

        super_img = package.images_dir / "super.img"
        if super_img.exists():
            package.logger.info(
                f"[{package.label}] Found super.img, unpacking logical partitions..."
            )

            try:
                if partitions:
                    package.logger.info(
                        f"[{package.label}] Unpacking specific partitions: {partitions}"
                    )
                    for part in partitions:
                        cmd = [
                            "lpunpack",
                            "-p",
                            part,
                            str(super_img),
                            str(package.images_dir),
                        ]
                        package.shell.run(cmd, check=False)
                        cmd_a = [
                            "lpunpack",
                            "-p",
                            f"{part}_a",
                            str(super_img),
                            str(package.images_dir),
                        ]
                        package.shell.run(cmd_a, check=False)
                else:
                    package.logger.info(
                        f"[{package.label}] Unpacking ALL partitions from super.img..."
                    )
                    package.shell.run(["lpunpack", str(super_img), str(package.images_dir)])

            except Exception as e:
                package.logger.error(f"Failed to unpack super.img: {e}")
                raise
            finally:
                if super_img.exists():
                    os.remove(super_img)


def extract_local(
    package: RomPackage,
    partitions: Optional[List[str]],
) -> None:
    """Handle local directory mode (pre-extracted).

    Args:
        package: The RomPackage instance.
        partitions: List of partitions to process.
    """
    package.logger.info(f"[{package.label}] Local dir mode, skipping payload extraction.")


class ImageExtractor:
    """Handles ROM image extraction logic."""

    def __init__(self, package: RomPackage) -> None:
        self.package = package

    def extract_images(
        self,
        partitions: Optional[List[str]] = None,
        source_changed: bool = False,
        current_source_hash: str = "",
        source_hash_path: Path = None,  # type: ignore[assignment]
    ) -> None:
        """Execute ROM image extraction based on type.

        Args:
            partitions: List of partitions to extract (None = all logical partitions).
            source_changed: Whether the source file has changed.
            current_source_hash: Current hash of the source file.
            source_hash_path: Path to store the source hash.
        """
        from .constants import RomType

        try:
            if self.package.rom_type == RomType.PAYLOAD:
                extract_payload(self.package, partitions)
            elif self.package.rom_type == RomType.BROTLI:
                extract_brotli(self.package, partitions)
            elif self.package.rom_type == RomType.FASTBOOT:
                extract_fastboot(self.package, partitions)

        except Exception as e:
            self.package.logger.error(f"Image extraction failed: {e}")
            raise

        # Save hash after successful extraction if source changed
        if source_changed and source_hash_path is not None:
            try:
                with open(source_hash_path, "w") as f:
                    f.write(current_source_hash)
                self.package.logger.info(
                    f"[{self.package.label}] Saved source file hash for future change detection."
                )
            except Exception as e:
                self.package.logger.warning(
                    f"[{self.package.label}] Could not save source hash file: {e}"
                )
