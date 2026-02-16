import argparse
import logging
import sys
import shutil
from pathlib import Path

from src.core.apk_patcher import AppPatcher
from src.core.props import PropertyModifier
from src.core.modifier import FirmwareModifier, SystemModifier, FrameworkModifier, RomModifier
from src.core.packer import Repacker
from src.core.rom import RomPackage
from src.core.context import PortingContext

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
    parser.add_argument("--ksu", action="store_true", help="Inject KernelSU into init_boot")
    parser.add_argument("--work-dir", default="build", help="Working directory (default: build)")
    parser.add_argument("--clean", action="store_true", help="Clean working directory before starting")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
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
    
    logger.info("Starting HyperOS Porting Tool...")
    logger.info(f"Stock ROM: {args.stock}")
    logger.info(f"Port ROM:  {args.port}")
    logger.info(f"KSU:       {args.ksu}")
    logger.info(f"Work Dir:  {args.work_dir}")

    work_dir = Path(args.work_dir).resolve()
    
    if args.clean:
        clean_work_dir(work_dir)
    
    # Define working directories
    stock_work_dir = work_dir / "stockrom"
    port_work_dir = work_dir / "portrom"
    target_work_dir = work_dir / "target"

    try:
        # Initialize ROM packages
        stock = RomPackage(args.stock, stock_work_dir, label="Stock")
        port = RomPackage(args.port, port_work_dir, label="Port")
        
        # Define port ROM partitions to extract
        port_partitions = ["system", "product", "system_ext", "mi_ext"]

        # Execute Phase 1: Image Extraction
        logger.info(">>> Phase 1: Extraction")
        stock.extract_images() # Extract all from stock
        port.extract_images(port_partitions) # Extract specific from port

        # Execute Phase 2: Context Initialization
        logger.info(">>> Phase 2: Initialization")
        ctx = PortingContext(stock, port, target_work_dir)
        ctx.enable_ksu = args.ksu
        ctx.initialize_target()

        logger.info(f"Detected Stock ROM Type: {stock.rom_type}")
        
        # Export properties for debug analysis
        stock.export_props(work_dir / "stock_debug.prop")
        port.export_props(work_dir / "port_debug.prop")
        
        # Identify stock and port device models
        stock_device = stock.get_prop("ro.product.name_for_attestation")
        port_device = port.get_prop("ro.product.name_for_attestation")
        logger.info(f"Stock Device: {stock_device}")
        logger.info(f"Port Device:  {port_device}")

        # Execute Phase 3: System Modification
        logger.info(">>> Phase 3: Modification")
        
        # System modifications
        SystemModifier(ctx).run()
        
        # Property modifications
        PropertyModifier(ctx).run()
        
        # Framework modifications
        framework_modifier = FrameworkModifier(ctx)
        framework_modifier.run()
        
        # Firmware modifications
        FirmwareModifier(ctx).run()
        
        # General ROM modifications
        RomModifier(ctx).run_all_modifications()
        
        # App patching
        AppPatcher(ctx, framework_modifier).run()

        # Execute Phase 4: Image Repacking
        logger.info(">>> Phase 4: Repacking")
        packer = Repacker(ctx)
        packer.pack_all(pack_type="EROFS", is_rw=False)
        
        logger.info(f"All images packed successfully! Check {target_work_dir}/*.img")

        # Generate OTA payload (Optional)
        packer.pack_ota_payload() 

    except Exception as e:
        logger.error(f"An error occurred during porting: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
