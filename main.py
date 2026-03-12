import argparse
import logging
import sys
import shutil
from pathlib import Path

from src.core.modifiers import (
    UnifiedModifier,
    FirmwareModifier,
    FrameworkModifier,
    RomModifier,
)
from src.core.packer import Repacker
from src.core.rom import RomPackage
from src.core.context import PortingContext
from src.core.config_loader import load_device_config
from src.utils.downloader import RomDownloader
from src.utils.otatools_manager import OtaToolsManager
from src.core.cache_manager import PortRomCacheManager


# Set up logging
def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("porting.log", mode="w"),
        ],
    )


logger = logging.getLogger("main")


def parse_args():
    parser = argparse.ArgumentParser(description="HyperOS Porting Tool")
    parser.add_argument("--stock", required=True, help="Path to Stock ROM (zip/payload/dir)")
    parser.add_argument(
        "--port",
        required=False,
        help="Path to Port ROM (zip/payload/dir). If omitted, runs in Official Modification mode.",
    )
    parser.add_argument(
        "--ksu",
        action="store_true",
        help="Inject KernelSU into init_boot/boot. Default: from config or False",
    )
    parser.add_argument("--work-dir", default="build", help="Working directory (default: build)")
    parser.add_argument(
        "--clean", action="store_true", help="Clean working directory before starting"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--pack-type",
        choices=["super", "payload"],
        default=None,
        help="Output format: super (Super Image/Fastboot) or payload (OTA Payload/Recovery). Default: from config or 'payload'",
    )
    parser.add_argument(
        "--fs-type",
        choices=["erofs", "ext4"],
        default=None,
        help="Filesystem type for repacking. Default: from config or 'erofs'",
    )
    parser.add_argument("--eu-bundle", help="Path/URL to EU Localization Bundle zip")
    parser.add_argument(
        "--phases",
        nargs="+",
        help="Specific phases to run: system, apk, framework, firmware, repack (default: all)",
    )
    parser.add_argument(
        "--cache-dir",
        default=".cache/portroms",
        help="Cache directory for Port ROM reuse (default: .cache/portroms)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache, force full extraction and modification",
    )
    parser.add_argument(
        "--no-partition-cache",
        action="store_true",
        help="Disable partition-level caching (APK caching still works)",
    )
    parser.add_argument(
        "--clear-cache", action="store_true", help="Clear all cache before starting"
    )
    parser.add_argument(
        "--show-cache-stats", action="store_true", help="Show cache statistics and exit"
    )

    args = parser.parse_args()

    # Handle comma-separated phases (e.g., "system,apk" -> ["system", "apk"])
    if args.phases:
        expanded_phases = []
        for phase in args.phases:
            expanded_phases.extend(phase.split(","))
        args.phases = [p.strip() for p in expanded_phases if p.strip()]

        # Validate phases
        valid_phases = ["system", "apk", "framework", "firmware", "repack"]
        invalid = [p for p in args.phases if p not in valid_phases]
        if invalid:
            parser.error(
                f"invalid choice: {', '.join(invalid)} (choose from {', '.join(valid_phases)})"
            )

    return args


def clean_work_dir(work_dir: Path):
    if work_dir.exists():
        logger.warning(f"Cleaning working directory: {work_dir}")
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)


def main():
    args = parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(log_level)

    is_official_modify = args.port is None
    if is_official_modify:
        logger.info("No Port ROM provided. Entering Official Modification mode.")
        args.port = args.stock

    # Initialize cache manager
    cache_manager = None
    if not args.no_cache and not is_official_modify:
        # Check if partition cache should be disabled
        cache_partitions = not args.no_partition_cache
        if not cache_partitions:
            logger.info("Partition-level caching disabled by CLI argument")

        cache_manager = PortRomCacheManager(args.cache_dir, cache_partitions=cache_partitions)

        if args.show_cache_stats:
            import json

            stats = cache_manager.get_cache_info()
            print(json.dumps(stats, indent=2))
            sys.exit(0)

        if args.clear_cache:
            cache_manager.clear_all()
            logger.info("Cache cleared")

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
    if cache_manager:
        logger.info(f"Cache:     Enabled ({args.cache_dir})")
    else:
        logger.info("Cache:     Disabled")
    logger.info("=" * 70)

    # Check and download otatools if needed
    otatools_manager = OtaToolsManager()
    if not otatools_manager.ensure_otatools():
        logger.error("Failed to locate or download otatools. Exiting.")
        sys.exit(1)

    # Handle URL Downloads
    downloader = RomDownloader()
    if args.stock.startswith("http"):
        logger.info("Downloading Stock ROM...")
        args.stock = str(downloader.download(args.stock))

    # Re-evaluate port if it was set from stock
    if is_official_modify:
        args.port = args.stock

    if not is_official_modify and args.port.startswith("http"):
        logger.info("Downloading Port ROM...")
        args.port = str(downloader.download(args.port))

    if args.eu_bundle and args.eu_bundle.startswith("http"):
        logger.info("Downloading EU Bundle...")
        args.eu_bundle = str(downloader.download(args.eu_bundle))

    work_dir = Path(args.work_dir).resolve()

    if args.clean:
        clean_work_dir(work_dir)

    # Define working directories
    stock_work_dir = work_dir / "stockrom"
    port_work_dir = work_dir / "portrom"
    target_work_dir = work_dir / "target"

    try:
        # Execute Phase 1: Image Extraction
        logger.info(">>> Phase 1: Extraction")
        stock = RomPackage(args.stock, stock_work_dir, label="Stock")
        stock.extract_images()

        if is_official_modify:
            # In official modify mode, port is the same as stock
            port = stock
        else:
            port = RomPackage(args.port, port_work_dir, label="Port", cache_manager=cache_manager)
            port_partitions = ["system", "product", "system_ext", "mi_ext"]
            port.extract_images(port_partitions)

        # Execute Phase 2: Context Initialization
        logger.info(">>> Phase 2: Initialization")
        ctx = PortingContext(stock, port, target_work_dir, is_official_modify=is_official_modify)

        # Set cache manager for APK-level caching
        ctx.cache_manager = cache_manager

        # Set dynamic attributes
        ctx.eu_bundle = args.eu_bundle  # type: ignore
        ctx.initialize_target()

        # Load device configuration
        stock_device_code = (
            stock.get_prop("ro.product.name_for_attestation")
            or stock.get_prop("ro.product.vendor.device")
            or "unknown"
        )
        device_config = load_device_config(stock_device_code, logger)

        # Store device config in context for plugins
        ctx.device_config = device_config  # type: ignore

        # Check cache configuration
        if cache_manager and not device_config.get("cache", {}).get("partitions", True):
            logger.info("Partition-level caching disabled by device config")
            cache_manager.cache_partitions = False

        # Determine settings
        enable_ksu = args.ksu or device_config.get("ksu", {}).get("enable", False)
        ctx.enable_ksu = enable_ksu
        logger.info(
            f"KernelSU: {'enabled' if enable_ksu else 'disabled'} (from {'CLI' if args.ksu else 'config'})"
        )

        pack_type = args.pack_type or device_config.get("pack", {}).get("type", "payload")
        fs_type = args.fs_type or device_config.get("pack", {}).get("fs_type", "erofs")

        logger.info(f"Pack Type: {pack_type} (from {'CLI' if args.pack_type else 'config'})")
        logger.info(f"Filesystem: {fs_type} (from {'CLI' if args.fs_type else 'config'})")
        logger.info(f"Detected Stock ROM Type: {stock.rom_type}")

        # Export properties for debug analysis
        stock.export_props(work_dir / "stock_debug.prop")
        port.export_props(work_dir / "port_debug.prop")

        # Identify devices
        stock_device = stock.get_prop("ro.product.name_for_attestation")
        port_device = port.get_prop("ro.product.name_for_attestation")
        logger.info(f"Stock Device: {stock_device}")
        logger.info(f"Port Device:  {port_device}")

        # Execute Phase 3: Modifications
        logger.info(">>> Phase 3: Modifications")

        # Use UnifiedModifier for system + APK modifications
        phases_to_run = args.phases if args.phases else ["system", "apk", "framework", "firmware"]

        if "system" in phases_to_run or "apk" in phases_to_run:
            logger.info("Running Unified Modifier (System + APK)...")
            unified_modifier = UnifiedModifier(ctx, enable_apk_mods=("apk" in phases_to_run))

            # Map phases to unified modifier format
            unified_phases = []
            if "system" in phases_to_run:
                unified_phases.append("system")
            if "apk" in phases_to_run:
                unified_phases.append("apk")

            if unified_phases:
                success = unified_modifier.run(phases=unified_phases)
                if not success:
                    logger.warning("Some modifications failed, continuing...")

        # Framework modifications (separate from unified for now)
        if "framework" in phases_to_run:
            logger.info("Running Framework Modifier...")
            framework_modifier = FrameworkModifier(ctx)
            framework_modifier.run()

        # Firmware modifications
        if "firmware" in phases_to_run:
            logger.info("Running Firmware Modifier...")
            FirmwareModifier(ctx).run()

        # ROM-level modifications (device overlays, etc.)
        RomModifier(ctx).run_all_modifications()

        # Execute Phase 4: Image Repacking
        if "repack" in phases_to_run or not args.phases:
            logger.info(">>> Phase 4: Repacking")

            packer = Repacker(ctx)
            packer.pack_all(pack_type=fs_type.upper(), is_rw=(fs_type == "ext4"))

            logger.info(f"All images packed successfully! Check {target_work_dir}/*.img")

            # Execute Packing Strategy
            if pack_type == "super":
                logger.info("Generating Super Image...")
                packer.pack_super_image()
            else:
                logger.info("Generating OTA Payload...")
                packer.pack_ota_payload()

        logger.info("=" * 70)
        logger.info("Porting completed successfully!")

        # Show cache statistics
        if cache_manager:
            import json

            stats = cache_manager.get_cache_info()
            if stats["cached_roms"]:
                total_mb = stats.get("total_size_mb", 0)
                logger.info(
                    f"Cache: {len(stats['cached_roms'])} ROMs cached, {total_mb:.1f} MB total"
                )

        logger.info("=" * 70)

        sys.exit(0)

    except KeyboardInterrupt:
        logger.warning("\nOperation cancelled by user")
        sys.exit(130)

    except Exception as e:
        logger.error(f"An error occurred during porting: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
