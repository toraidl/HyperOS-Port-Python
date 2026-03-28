"""Workflow orchestration helpers for the HyperOS porting CLI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from src.app.bootstrap import clean_work_dir, initialize_cache_manager
from src.app.diff_report import collect_artifact_state, generate_diff_report, save_diff_report
from src.app.preflight import run_preflight, save_preflight_report
from src.app.snapshots import StageSnapshotManager
from src.core.config_loader import load_device_config
from src.core.context import PortingContext
from src.core.device_auto_config import get_or_create_device_config
from src.core.modifiers import FirmwareModifier, FrameworkModifier, RomModifier, UnifiedModifier
from src.core.packer import Repacker
from src.core.rom import RomPackage
from src.utils.downloader import RomDownloader
from src.utils.otatools_manager import OtaToolsManager

DEFAULT_PHASES = ["system", "apk", "framework", "firmware"]
REPACK_CHECKPOINT_NAME = "repack-context.json"


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

    pack_cfg = ctx.device_config.get("pack", {})
    config_custom_avb_chain = False
    if isinstance(pack_cfg, dict):
        config_custom_avb_chain = bool(pack_cfg.get("custom_avb_chain", False))
    ctx.enable_custom_avb_chain = bool(args.custom_avb_chain or config_custom_avb_chain)
    logger.info(
        "Custom AVB chain: %s (from %s)",
        "enabled" if ctx.enable_custom_avb_chain else "disabled",
        "CLI" if args.custom_avb_chain else "config",
    )

    pack_type = args.pack_type or ctx.device_config.get("pack", {}).get("type", "payload")
    fs_type = args.fs_type or ctx.device_config.get("pack", {}).get("fs_type", "erofs")
    logger.info(f"Pack Type: {pack_type} (from {'CLI' if args.pack_type else 'config'})")
    logger.info(f"Filesystem: {fs_type} (from {'CLI' if args.fs_type else 'config'})")
    stock_rom_type = "unknown"
    stock = getattr(ctx, "stock", None)
    if stock is not None:
        rom_type = getattr(stock, "rom_type", None)
        if rom_type is not None:
            stock_rom_type = str(rom_type)
    logger.info("Detected Stock ROM Type: %s", stock_rom_type)
    return pack_type, fs_type


def run_modification_phases(
    ctx: PortingContext, phases_to_run: list[str], logger: logging.Logger
) -> None:
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


def _checkpoint_path(work_dir: Path) -> Path:
    return work_dir / REPACK_CHECKPOINT_NAME


def save_repack_checkpoint(ctx: PortingContext, work_dir: Path) -> Path:
    def as_str(value, default: str = "") -> str:
        return value if isinstance(value, str) else default

    def as_bool(value, default: bool = False) -> bool:
        return bool(value) if isinstance(value, (bool, int, str)) else default

    raw_device_config = getattr(ctx, "device_config", {})
    device_config = raw_device_config if isinstance(raw_device_config, dict) else {}
    payload = {
        "stock_rom_code": as_str(getattr(ctx, "stock_rom_code", ""), "unknown"),
        "target_rom_version": as_str(getattr(ctx, "target_rom_version", ""), ""),
        "security_patch": as_str(getattr(ctx, "security_patch", ""), "Unknown"),
        "is_ab_device": as_bool(getattr(ctx, "is_ab_device", False), False),
        "base_android_version": as_str(getattr(ctx, "base_android_version", ""), "0"),
        "port_android_version": as_str(getattr(ctx, "port_android_version", ""), "0"),
        "is_port_eu_rom": as_bool(getattr(ctx, "is_port_eu_rom", False), False),
        "is_port_global_rom": as_bool(getattr(ctx, "is_port_global_rom", False), False),
        "port_global_region": as_str(getattr(ctx, "port_global_region", ""), ""),
        "device_config": device_config,
    }
    path = _checkpoint_path(work_dir)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_repack_checkpoint(work_dir: Path, target_work_dir: Path, logger: logging.Logger):
    path = _checkpoint_path(work_dir)
    if not path.exists():
        raise FileNotFoundError(f"Repack checkpoint not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    def get_target_prop_file(part_name: str):
        part_dir: Path = target_work_dir / part_name
        candidates = [
            part_dir / "build.prop",
            part_dir / "system" / "build.prop",
            part_dir / "etc" / "build.prop",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    ctx = SimpleNamespace(
        stock_rom_code=data.get("stock_rom_code", "unknown"),
        target_rom_version=data.get("target_rom_version", ""),
        security_patch=data.get("security_patch", "Unknown"),
        is_ab_device=bool(data.get("is_ab_device", False)),
        base_android_version=data.get("base_android_version", "0"),
        port_android_version=data.get("port_android_version", "0"),
        is_port_eu_rom=bool(data.get("is_port_eu_rom", False)),
        is_port_global_rom=bool(data.get("is_port_global_rom", False)),
        port_global_region=data.get("port_global_region", ""),
        target_dir=target_work_dir,
        target_config_dir=target_work_dir / "config",
        repack_images_dir=target_work_dir / "repack_images",
        device_config=data.get("device_config", {}),
        enable_ksu=False,
        enable_custom_avb_chain=False,
        get_target_prop_file=get_target_prop_file,
    )
    logger.info("Loaded repack checkpoint from %s", path)
    return ctx


def log_diff_report_summary(diff_report: dict[str, object], logger: logging.Logger) -> None:
    """Log a compact summary for generated artifact diff reports."""
    summary = diff_report.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}

    logger.info(
        "Artifact diff summary: +%s -%s ~%s props=%s apks=%s risks=%s",
        summary.get("files_added", 0),
        summary.get("files_removed", 0),
        summary.get("files_modified", 0),
        summary.get("prop_changes", 0),
        summary.get("apk_changes", 0),
        summary.get("risk_flags", 0),
    )

    highlights = diff_report.get("highlights", {})
    if not isinstance(highlights, dict):
        return
    risk_flags = highlights.get("risk_flags", [])
    if not isinstance(risk_flags, list) or not risk_flags:
        return

    codes: list[str] = []
    for flag in risk_flags:
        if not isinstance(flag, dict):
            continue
        code = flag.get("code")
        if isinstance(code, str):
            codes.append(code)
    if codes:
        logger.warning("Artifact diff risk flags: %s", ", ".join(codes))


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return int(value)
        except ValueError:
            return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def build_super_size_check(
    stock_device_code: str,
    device_config: dict[str, object],
) -> dict[str, object]:
    partition_info_path = Path("devices") / stock_device_code / "partition_info.json"
    pack_cfg = device_config.get("pack") if isinstance(device_config, dict) else None
    config_super_size = _to_int(pack_cfg.get("super_size")) if isinstance(pack_cfg, dict) else None

    partition_info_super_size: int | None = None
    if partition_info_path.exists():
        try:
            payload = json.loads(partition_info_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                partition_info_super_size = _to_int(payload.get("super_size"))
        except (OSError, json.JSONDecodeError):
            partition_info_super_size = None

    mismatch = (
        config_super_size is not None
        and partition_info_super_size is not None
        and config_super_size != partition_info_super_size
    )
    return {
        "partition_info_path": str(partition_info_path),
        "partition_info_exists": partition_info_path.exists(),
        "device_config_super_size": config_super_size,
        "partition_info_super_size": partition_info_super_size,
        "mismatch": mismatch,
    }


def inject_super_size_check_into_diff_report(
    diff_report: dict[str, object],
    super_size_check: dict[str, object],
) -> None:
    checks = diff_report.get("checks")
    if not isinstance(checks, dict):
        checks = {}
        diff_report["checks"] = checks
    checks["super_size"] = super_size_check

    if not super_size_check.get("mismatch"):
        return

    highlights = diff_report.get("highlights")
    if not isinstance(highlights, dict):
        highlights = {}
        diff_report["highlights"] = highlights

    risk_flags = highlights.get("risk_flags")
    if not isinstance(risk_flags, list):
        risk_flags = []
        highlights["risk_flags"] = risk_flags

    risk_flags.append(
        {
            "code": "SUPER_SIZE_MISMATCH",
            "message": "Device config super_size differs from partition_info.json super_size.",
            "details": {
                "device_config_super_size": super_size_check.get("device_config_super_size"),
                "partition_info_super_size": super_size_check.get("partition_info_super_size"),
            },
        }
    )

    summary = diff_report.get("summary")
    if isinstance(summary, dict):
        summary["risk_flags"] = len(risk_flags)


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

    if getattr(args, "resume_from_packer", False):
        logger.info("Resume mode: packer-only repacking from existing target workspace.")
        try:
            ctx = load_repack_checkpoint(work_dir, target_work_dir, logger)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.error(str(exc))
            return 2
        pack_type, fs_type = determine_pack_settings(args, ctx, logger)
        run_repacking(ctx, ["repack"], pack_type, fs_type, target_work_dir, logger)
        logger.info("Repack-only resume completed successfully.")
        return 0

    snapshot_manager = (
        StageSnapshotManager(args.snapshot_dir or (work_dir / "snapshots"), logger)
        if args.enable_snapshots or args.rollback_to_snapshot
        else None
    )

    if args.rollback_to_snapshot:
        if not snapshot_manager:
            logger.error("Snapshot manager is not available.")
            return 1
        try:
            snapshot_manager.restore(args.rollback_to_snapshot, target_work_dir)
            logger.info(f"Rollback completed from snapshot: {args.rollback_to_snapshot}")
            return 0
        except FileNotFoundError as exc:
            logger.error(str(exc))
            available = snapshot_manager.list_snapshot_names()
            if available:
                logger.info(f"Available snapshots: {', '.join(available)}")
            return 2

    if not args.skip_preflight:
        preflight_report = run_preflight(args, is_official_modify, logger)
        report_path = save_preflight_report(preflight_report, args.preflight_report)
        logger.info(f"Preflight report saved to: {report_path}")
        if preflight_report.has_failures(strict=args.preflight_strict):
            mode = "strict mode (blockers + risks)" if args.preflight_strict else "blockers"
            logger.error(f"Preflight checks failed ({mode}). Aborting.")
            return 2
        if args.preflight_only:
            logger.info("Preflight completed with no blockers. Exiting by request.")
            return 0
    elif args.preflight_only:
        logger.warning("Ignoring --preflight-only because --skip-preflight is set.")
        return 0

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
    if snapshot_manager:
        snapshot_manager.capture("phase2_initialized", target_work_dir)

    # Get stock device code from props or payload metadata
    stock_device_code = (
        stock.get_prop("ro.product.name_for_attestation")
        or stock.get_prop("ro.product.vendor.device")
        or "unknown"
    )

    device_config_dir = Path("devices") / stock_device_code
    if not device_config_dir.exists():
        logger.info(
            f"No device config found for {stock_device_code}, attempting auto-configuration..."
        )
    else:
        logger.info(
            "Detected existing device config for %s, ensuring partition_info.json is present.",
            stock_device_code,
        )
    try:
        ctx.device_config = get_or_create_device_config(
            device_code=stock_device_code,
            payload_path=Path(args.stock) if stock.rom_type.name == "PAYLOAD" else None,
            stock_props=stock.props,
            logger=logger,
            payload_info=stock.payload_info,
        )
    except Exception as e:
        logger.warning(f"Device config initialization failed: {e}")
        logger.info("Falling back to common config")
        ctx.device_config = load_device_config(stock_device_code, logger)

    super_size_check = build_super_size_check(stock_device_code, ctx.device_config)
    if super_size_check.get("mismatch"):
        logger.warning(
            "Detected super_size mismatch: config=%s, partition_info=%s",
            super_size_check.get("device_config_super_size"),
            super_size_check.get("partition_info_super_size"),
        )

    if cache_manager and ctx.device_config.get("cache", {}).get("partitions", False):
        logger.info("Partition-level caching enabled by device config")
        cache_manager.cache_partitions = True

    pack_type, fs_type = determine_pack_settings(args, ctx, logger)
    checkpoint_path = save_repack_checkpoint(ctx, work_dir)
    logger.info("Saved repack checkpoint to: %s", checkpoint_path)

    work_dir.mkdir(parents=True, exist_ok=True)
    stock.export_props(work_dir / "stock_debug.prop")
    port.export_props(work_dir / "port_debug.prop")
    logger.info(f"Stock Device: {stock.get_prop('ro.product.name_for_attestation')}")
    logger.info(f"Port Device:  {port.get_prop('ro.product.name_for_attestation')}")

    phases_to_run = args.phases if args.phases else list(DEFAULT_PHASES)
    baseline_artifact_state = (
        collect_artifact_state(target_work_dir, logger) if args.enable_diff_report else None
    )
    run_modification_phases(ctx, phases_to_run, logger)
    if snapshot_manager:
        snapshot_manager.capture("phase3_modified", target_work_dir)

    run_repacking(ctx, phases_to_run, pack_type, fs_type, target_work_dir, logger)
    if snapshot_manager and ("repack" in phases_to_run or phases_to_run == DEFAULT_PHASES):
        snapshot_manager.capture("phase4_repacked", target_work_dir)
    if args.enable_diff_report and baseline_artifact_state is not None:
        final_artifact_state = collect_artifact_state(target_work_dir, logger)
        diff_report = generate_diff_report(baseline_artifact_state, final_artifact_state)
        inject_super_size_check_into_diff_report(diff_report, super_size_check)
        report_path = save_diff_report(diff_report, args.diff_report)
        logger.info(f"Artifact diff report saved to: {report_path}")
        log_diff_report_summary(diff_report, logger)

    logger.info("=" * 70)
    logger.info("Porting completed successfully!")
    if cache_manager:
        stats = cache_manager.get_cache_info()
        if stats["cached_roms"]:
            total_mb = stats.get("total_size_mb", 0)
            logger.info(f"Cache: {len(stats['cached_roms'])} ROMs cached, {total_mb:.1f} MB total")
    logger.info("=" * 70)
    return 0
