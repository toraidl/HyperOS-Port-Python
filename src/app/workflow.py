"""Workflow orchestration helpers for the HyperOS porting CLI."""

from __future__ import annotations

import logging
from pathlib import Path

from src.app.bootstrap import clean_work_dir, initialize_cache_manager
from src.core.config_loader import load_device_config
from src.core.context import PortingContext
from src.core.modifiers import FirmwareModifier, FrameworkModifier, RomModifier, UnifiedModifier
from src.core.packer import Repacker
from src.core.rom import RomPackage
from src.utils.downloader import RomDownloader
from src.utils.otatools_manager import OtaToolsManager

DEFAULT_PHASES = ["system", "apk", "framework", "firmware"]


def resolve_work_paths(work_dir: str | Path) -> tuple[Path, Path, Path, Path]:
    """Resolve the working directory and the standard ROM subdirectories."""
    root = Path(work_dir).resolve()
    return root, root / "stockrom", root / "portrom", root / "target"


def resolve_remote_inputs(args, is_official_modify: bool, logger: logging.Logger) -> None:
    """Download remote stock/port/bundle inputs in place when needed."""
    downloader = RomDownloader()
    if args.stock.startswith("http"):
        logger.info("Downloading Stock ROM...")
        args.stock = str(downloader.download(args.stock))

    if is_official_modify:
        args.port = args.stock

    if not is_official_modify and args.port.startswith("http"):
        logger.info("Downloading Port ROM...")
        args.port = str(downloader.download(args.port))

    if args.eu_bundle and args.eu_bundle.startswith("http"):
        logger.info("Downloading EU Bundle...")
        args.eu_bundle = str(downloader.download(args.eu_bundle))


def log_run_configuration(
    logger: logging.Logger, args, is_official_modify: bool, cache_enabled: bool
) -> None:
    """Log the resolved runtime configuration."""
    logger.info("=" * 70)
    logger.info("HyperOS Porting Tool v2.0")
    logger.info("=" * 70)
    logger.info(f"Stock ROM: {args.stock}")
    if is_official_modify:
        logger.info("Mode:      Official Modification")
    else:
        logger.info(f"Port ROM:  {args.port}")
    logger.info(f"KSU:       {args.ksu}")
    logger.info(f"Work Dir:  {args.work_dir}")
    if args.phases:
        logger.info(f"Phases:    {', '.join(args.phases)}")
    logger.info(f"Cache:     {'Enabled' if cache_enabled else 'Disabled'}")
    logger.info("=" * 70)


def determine_pack_settings(args, ctx: PortingContext, logger: logging.Logger) -> tuple[str, str]:
    """Determine final packing settings from CLI flags and device config."""
    enable_ksu = args.ksu or ctx.device_config.get("ksu", {}).get("enable", False)
    ctx.enable_ksu = enable_ksu
    logger.info(
        f"KernelSU: {'enabled' if enable_ksu else 'disabled'} "
        f"(from {'CLI' if args.ksu else 'config'})"
    )

    pack_type = args.pack_type or ctx.device_config.get("pack", {}).get("type", "payload")
    fs_type = args.fs_type or ctx.device_config.get("pack", {}).get("fs_type", "erofs")
    logger.info(f"Pack Type: {pack_type} (from {'CLI' if args.pack_type else 'config'})")
    logger.info(f"Filesystem: {fs_type} (from {'CLI' if args.fs_type else 'config'})")
    logger.info(f"Detected Stock ROM Type: {ctx.stock.rom_type}")
    return pack_type, fs_type


def run_modification_phases(ctx: PortingContext, phases_to_run: list[str], logger: logging.Logger) -> None:
    """Run the requested modification phases."""
    logger.info(">>> Phase 3: Modifications")

    if "system" in phases_to_run or "apk" in phases_to_run:
        logger.info("Running Unified Modifier (System + APK)...")
        unified_modifier = UnifiedModifier(ctx, enable_apk_mods=("apk" in phases_to_run))
        unified_phases = [phase for phase in ("system", "apk") if phase in phases_to_run]
        if unified_phases and not unified_modifier.run(phases=unified_phases):
            logger.warning("Some modifications failed, continuing...")

    if "framework" in phases_to_run:
        logger.info("Running Framework Modifier...")
        FrameworkModifier(ctx).run()

    if "firmware" in phases_to_run:
        logger.info("Running Firmware Modifier...")
        FirmwareModifier(ctx).run()

    RomModifier(ctx).run_all_modifications()


def run_repacking(
    ctx: PortingContext,
    phases_to_run: list[str],
    pack_type: str,
    fs_type: str,
    target_work_dir: Path,
    logger: logging.Logger,
) -> None:
    """Run the repacking and final image generation steps."""
    if "repack" not in phases_to_run and phases_to_run != DEFAULT_PHASES:
        return

    logger.info(">>> Phase 4: Repacking")
    packer = Repacker(ctx)
    packer.pack_all(pack_type=fs_type.upper(), is_rw=(fs_type == "ext4"))
    logger.info(f"All images packed successfully! Check {target_work_dir}/*.img")

    if pack_type == "super":
        logger.info("Generating Super Image...")
        packer.pack_super_image()
    else:
        logger.info("Generating OTA Payload...")
        packer.pack_ota_payload()


def execute_porting(args, logger: logging.Logger) -> int:
    """Execute the end-to-end porting workflow and return a process exit code."""
    is_official_modify = args.port is None
    if is_official_modify:
        logger.info("No Port ROM provided. Entering Official Modification mode.")
        args.port = args.stock

    cache_bootstrap = initialize_cache_manager(args, is_official_modify, logger)
    if cache_bootstrap.exit_code is not None:
        return cache_bootstrap.exit_code
    cache_manager = cache_bootstrap.cache_manager

    log_run_configuration(logger, args, is_official_modify, cache_enabled=cache_manager is not None)

    otatools_manager = OtaToolsManager()
    if not otatools_manager.ensure_otatools():
        logger.error("Failed to locate or download otatools. Exiting.")
        return 1

    resolve_remote_inputs(args, is_official_modify, logger)
    work_dir, stock_work_dir, port_work_dir, target_work_dir = resolve_work_paths(args.work_dir)

    if args.clean:
        clean_work_dir(work_dir, logger)

    logger.info(">>> Phase 1: Extraction")
    stock = RomPackage(args.stock, stock_work_dir, label="Stock")
    stock.extract_images()

    if is_official_modify:
        port = stock
    else:
        port = RomPackage(args.port, port_work_dir, label="Port", cache_manager=cache_manager)
        port.extract_images(["system", "product", "system_ext", "mi_ext"])

    logger.info(">>> Phase 2: Initialization")
    ctx = PortingContext(stock, port, target_work_dir, is_official_modify=is_official_modify)
    ctx.cache_manager = cache_manager
    ctx.eu_bundle = args.eu_bundle
    ctx.initialize_target(clean_existing=True)

    stock_device_code = (
        stock.get_prop("ro.product.name_for_attestation")
        or stock.get_prop("ro.product.vendor.device")
        or "unknown"
    )
    ctx.device_config = load_device_config(stock_device_code, logger)

    if cache_manager and ctx.device_config.get("cache", {}).get("partitions", False):
        logger.info("Partition-level caching enabled by device config")
        cache_manager.cache_partitions = True

    pack_type, fs_type = determine_pack_settings(args, ctx, logger)

    work_dir.mkdir(parents=True, exist_ok=True)
    stock.export_props(work_dir / "stock_debug.prop")
    port.export_props(work_dir / "port_debug.prop")
    logger.info(f"Stock Device: {stock.get_prop('ro.product.name_for_attestation')}")
    logger.info(f"Port Device:  {port.get_prop('ro.product.name_for_attestation')}")

    phases_to_run = args.phases if args.phases else list(DEFAULT_PHASES)
    run_modification_phases(ctx, phases_to_run, logger)
    run_repacking(ctx, phases_to_run, pack_type, fs_type, target_work_dir, logger)

    logger.info("=" * 70)
    logger.info("Porting completed successfully!")
    if cache_manager:
        stats = cache_manager.get_cache_info()
        if stats["cached_roms"]:
            total_mb = stats.get("total_size_mb", 0)
            logger.info(
                f"Cache: {len(stats['cached_roms'])} ROMs cached, {total_mb:.1f} MB total"
            )
    logger.info("=" * 70)
    return 0
