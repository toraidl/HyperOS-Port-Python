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

# Optional monitoring integration
try:
    from src.core.monitoring import Monitor, get_monitor
    MONITORING_AVAILABLE = True
except ImportError:
    MONITORING_AVAILABLE = False
    get_monitor = None

# Set up logging
def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("porting.log", mode='w')
        ]
    )

logger = logging.getLogger("main")

def parse_args():
    parser = argparse.ArgumentParser(description="HyperOS Porting Tool")
    parser.add_argument("--stock", required=True, help="Path to Stock ROM (zip/payload/dir)")
    parser.add_argument("--port", required=True, help="Path to Port ROM (zip/payload/dir)")
    parser.add_argument("--ksu", action="store_true", help="Inject KernelSU into init_boot/boot. Default: from config or False")
    parser.add_argument("--work-dir", default="build", help="Working directory (default: build)")
    parser.add_argument("--clean", action="store_true", help="Clean working directory before starting")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--monitor", action="store_true", help="Enable performance monitoring and generate report")
    parser.add_argument("--report-path", type=Path, default=Path("porting_report.json"), help="Path to save monitoring report")
    parser.add_argument("--pack-type", choices=["super", "payload"], default=None,
                        help="Output format: super (Super Image/Fastboot) or payload (OTA Payload/Recovery). Default: from config or 'payload'")
    parser.add_argument("--fs-type", choices=["erofs", "ext4"], default=None,
                        help="Filesystem type for repacking. Default: from config or 'erofs'")
    parser.add_argument("--eu-bundle", help="Path/URL to EU Localization Bundle zip")
    parser.add_argument("--phases", nargs="+", choices=["system", "apk", "framework", "firmware", "repack"], 
                        help="Specific phases to run (default: all)")
    return parser.parse_args()

def clean_work_dir(work_dir: Path):
    if work_dir.exists():
        logger.warning(f"Cleaning working directory: {work_dir}")
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

def main():
    args = parse_args()
    
    log_level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(log_level)
    
    logger.info("=" * 70)
    logger.info("HyperOS Porting Tool v2.0")
    logger.info("=" * 70)
    logger.info(f"Stock ROM: {args.stock}")
    logger.info(f"Port ROM:  {args.port}")
    logger.info(f"KSU:       {args.ksu}")
    logger.info(f"Work Dir:  {args.work_dir}")
    logger.info(f"Monitoring: {'enabled' if args.monitor else 'disabled'}")
    if args.phases:
        logger.info(f"Phases:    {', '.join(args.phases)}")
    logger.info("=" * 70)

    # Handle URL Downloads
    downloader = RomDownloader()
    if args.stock.startswith("http"):
        logger.info("Downloading Stock ROM...")
        args.stock = str(downloader.download(args.stock))
    
    if args.port.startswith("http"):
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

    # Initialize monitoring if requested
    monitor = None
    if args.monitor and MONITORING_AVAILABLE and get_monitor is not None:
        monitor = get_monitor()
        monitor.start()
        logger.info("Performance monitoring enabled")

    try:
        # Execute Phase 1: Image Extraction
        logger.info(">>> Phase 1: Extraction")
        if monitor:
            with monitor.phase("extraction"):
                stock = RomPackage(args.stock, stock_work_dir, label="Stock")
                port = RomPackage(args.port, port_work_dir, label="Port")
                
                port_partitions = ["system", "product", "system_ext", "mi_ext"]
                stock.extract_images()
                port.extract_images(port_partitions)
                
                monitor.record_metric("images_extracted", 8)
        else:
            stock = RomPackage(args.stock, stock_work_dir, label="Stock")
            port = RomPackage(args.port, port_work_dir, label="Port")
            
            port_partitions = ["system", "product", "system_ext", "mi_ext"]
            stock.extract_images()
            port.extract_images(port_partitions)

        # Execute Phase 2: Context Initialization
        logger.info(">>> Phase 2: Initialization")
        ctx = PortingContext(stock, port, target_work_dir)
        
        # Set dynamic attributes
        ctx.eu_bundle = args.eu_bundle  # type: ignore
        ctx.initialize_target()

        # Load device configuration
        stock_device_code = stock.get_prop("ro.product.name_for_attestation") or \
                           stock.get_prop("ro.product.vendor.device") or "unknown"
        device_config = load_device_config(stock_device_code, logger)
        
        # Store device config in context for plugins
        ctx.device_config = device_config  # type: ignore

        # Determine settings
        enable_ksu = args.ksu or device_config.get("ksu", {}).get("enable", False)
        ctx.enable_ksu = enable_ksu
        logger.info(f"KernelSU: {'enabled' if enable_ksu else 'disabled'} (from {'CLI' if args.ksu else 'config'})")

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
            
            if monitor:
                with monitor.phase("repacking"):
                    packer = Repacker(ctx)
                    packer.pack_all(pack_type=fs_type.upper(), is_rw=(fs_type == "ext4"))
                    monitor.record_metric("images_repacked", 8)
            else:
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

        # Finalize and generate report
        if monitor:
            monitor.stop()
            monitor.save_report(args.report_path)
            monitor.print_report()
            logger.info(f"Monitoring report saved to: {args.report_path}")

        logger.info("=" * 70)
        logger.info("Porting completed successfully!")
        logger.info("=" * 70)

    except KeyboardInterrupt:
        logger.warning("\nOperation cancelled by user")
        if monitor:
            monitor.stop()
            monitor.save_report(args.report_path)
        sys.exit(130)
        
    except Exception as e:
        logger.error(f"An error occurred during porting: {e}", exc_info=True)
        if monitor:
            monitor.report.add_error("main", e)
            monitor.stop()
            monitor.save_report(args.report_path)
        sys.exit(1)

if __name__ == "__main__":
    main()
