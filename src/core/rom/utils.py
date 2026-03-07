from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Union

if TYPE_CHECKING:
    from .package import RomPackage


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file for change detection.

    Args:
        file_path: Path to the file to hash.

    Returns:
        First 16 characters of the SHA-256 hex digest.
    """
    hash_sha256 = hashlib.sha256()

    with open(file_path, "rb") as f:
        # Read file in chunks to avoid memory issues with large files
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)

    # Return first 16 characters of the hash (similar to how git handles it)
    return hash_sha256.hexdigest()[:16]


def process_sparse_images(images_dir: Path, logger: logging.Logger, shell) -> None:
    """Merge/Convert sparse images (super.img.*, cust.img.*) using simg2img.

    Args:
        images_dir: Directory containing sparse images.
        logger: Logger instance for output.
        shell: ShellRunner instance for command execution.
    """
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
        logger.info(f"Using simg2img binary: {simg2img_bin}")
    else:
        logger.info("Using simg2img from system PATH")

    # 1. Handle super.img
    super_chunks = sorted(list(images_dir.glob("super.img.*")))
    target_super = images_dir / "super.img"

    if super_chunks:
        logger.info(f"Merging sparse super images: {[c.name for c in super_chunks]}...")
        try:
            cmd = [str(simg2img_bin)] + [str(c) for c in super_chunks] + [str(target_super)]
            shell.run(cmd)
            for c in super_chunks:
                os.unlink(c)
        except Exception as e:
            logger.error(f"Failed to merge super.img: {e}")
            raise

    elif target_super.exists():
        logger.info("converting super.img to raw (if sparse)...")
        temp_raw = images_dir / "super.raw.img"
        try:
            shell.run([str(simg2img_bin), str(target_super), str(temp_raw)])
            shutil.move(temp_raw, target_super)
        except Exception as e:
            logger.warning(f"simg2img conversion skipped/failed: {e}")
            if temp_raw.exists():
                os.unlink(temp_raw)

    # 2. Handle cust.img
    cust_chunks = sorted(list(images_dir.glob("cust.img.*")))
    target_cust = images_dir / "cust.img"

    if cust_chunks:
        logger.info("Merging sparse cust images...")
        try:
            cmd = [str(simg2img_bin)] + [str(c) for c in cust_chunks] + [str(target_cust)]
            shell.run(cmd)
            for c in cust_chunks:
                os.unlink(c)
        except Exception as e:
            logger.error(f"Failed to merge cust.img: {e}")


def load_single_prop_file(
    file_path: Path,
    extracted_dir: Path,
    props: dict,
    prop_history: dict,
    logger: logging.Logger,
) -> None:
    """Helper: Parse single file and update props dictionary.

    Args:
        file_path: Path to the .prop file.
        extracted_dir: Base extraction directory for relative path calculation.
        props: Dictionary to store property key-value pairs.
        prop_history: Dictionary to track property source history.
        logger: Logger instance for output.
    """
    try:
        rel_path = file_path.relative_to(extracted_dir)
    except ValueError:
        rel_path = file_path.name

    logger.debug(f"Parsing: {rel_path}")
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if key not in prop_history:
                    prop_history[key] = []
                prop_history[key].append((str(rel_path), value))
                props[key] = value
    except Exception as e:
        logger.error(f"Error reading {rel_path}: {e}")


def sort_prop_priority(path: Path) -> int:
    """Sort priority for build.prop files (lower = higher priority).

    Args:
        path: Path to the build.prop file.

    Returns:
        Priority number (0-99).
    """
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
