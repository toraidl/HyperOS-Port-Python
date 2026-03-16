"""Workspace preparation helpers for the porting workflow."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from src.core.rom import RomPackage

if TYPE_CHECKING:
    from src.core.context import PortingContext


def prepare_target_directories(ctx: "PortingContext", *, clean_existing: bool) -> None:
    """Prepare the target, config, and repack directories."""
    if ctx.target_dir.exists() and clean_existing:
        shutil.rmtree(ctx.target_dir)
    ctx.target_dir.mkdir(parents=True, exist_ok=True)
    ctx.target_config_dir.mkdir(parents=True, exist_ok=True)
    ctx.repack_images_dir.mkdir(parents=True, exist_ok=True)


def build_partition_layout(ctx: "PortingContext") -> dict[str, RomPackage]:
    """Build the source ROM mapping for each copied partition."""
    return {
        "vendor": ctx.stock,
        "odm": ctx.stock,
        "vendor_dlkm": ctx.stock,
        "odm_dlkm": ctx.stock,
        "system_dlkm": ctx.stock,
        "system": ctx.port,
        "system_ext": ctx.port,
        "product": ctx.port,
        "mi_ext": ctx.port,
        "product_dlkm": ctx.port,
    }


def install_partition(ctx: "PortingContext", part_name: str, source_rom: RomPackage) -> None:
    """Install a single partition from the source ROM into the target workspace."""
    src_dir = source_rom.extract_partition_to_file(part_name)
    if not src_dir or not src_dir.exists():
        ctx.logger.warning(f"Partition {part_name} missing in {source_rom.label}, skipping.")
        return

    dest_dir = ctx.target_dir / part_name
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    try:
        ctx.shell.run(["cp", "-a", "--reflink=auto", str(src_dir), str(dest_dir)])
    except Exception as exc:
        ctx.logger.error(f"Native copy failed, falling back to shutil: {exc}")
        try:
            shutil.copytree(src_dir, dest_dir, symlinks=True, dirs_exist_ok=True)
        except Exception as fallback_error:
            ctx.logger.error(f"Copy failed for {part_name}: {fallback_error}")

    src_fs, src_fc = source_rom.get_config_files(part_name)
    if src_fs.exists():
        shutil.copy2(src_fs, ctx.target_config_dir / f"{part_name}_fs_config")
    else:
        ctx.logger.warning(f"Missing fs_config for {part_name} in {source_rom.label}")

    if src_fc.exists():
        shutil.copy2(src_fc, ctx.target_config_dir / f"{part_name}_file_contexts")
    else:
        ctx.logger.warning(f"Missing file_contexts for {part_name} in {source_rom.label}")


def copy_firmware_images(ctx: "PortingContext", exclude_list: list[str]) -> None:
    """Copy firmware images that are not replaced by the target workspace."""
    ctx.logger.info("Copying firmware images from Base ROM...")
    if not ctx.stock.images_dir.exists():
        ctx.logger.warning("Stock images directory not found! Firmware copy skipped.")
        return

    copied_count = 0
    for img_file in ctx.stock.images_dir.glob("*.img"):
        part_name = img_file.stem
        clean_name = part_name.replace("_a", "").replace("_b", "")
        if clean_name in exclude_list:
            continue

        ctx.logger.debug(f"Copying firmware: {img_file.name}")
        shutil.copy2(img_file, ctx.repack_images_dir / img_file.name)
        copied_count += 1

    ctx.logger.info(f"Copied {copied_count} firmware images to {ctx.repack_images_dir}")
