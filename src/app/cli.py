"""CLI parsing helpers for the HyperOS porting entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

VALID_PHASES = ("system", "apk", "framework", "firmware", "repack")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
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
    parser.add_argument("--clean", action="store_true", help="Clean working directory before starting")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--pack-type",
        choices=["super", "payload"],
        default=None,
        help=(
            "Output format: super (Super Image/Fastboot) or payload (OTA Payload/Recovery). "
            "Default: from config or 'payload'"
        ),
    )
    parser.add_argument(
        "--fs-type",
        choices=["erofs", "ext4"],
        default=None,
        help="Filesystem type for repacking. Default: from config or 'erofs'",
    )
    parser.add_argument(
        "--custom-avb-chain",
        action="store_true",
        help="Enable custom AVB chain generation (re-sign images, rebuild vbmeta, verify chain)",
    )
    parser.add_argument(
        "--avb-key",
        type=Path,
        help="Path to custom AVB signing key (PEM format). Generate with: openssl genrsa -out key.pem 4096",
    )
    parser.add_argument(
        "--resume-from-packer",
        action="store_true",
        help="Resume from existing target workspace and run repacking only",
    )
    parser.add_argument("--eu-bundle", help="Path/URL to EU Localization Bundle zip")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run input preflight checks only, then exit",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight checks before porting workflow",
    )
    parser.add_argument(
        "--preflight-strict",
        action="store_true",
        help="Treat risk findings as failures in preflight checks",
    )
    parser.add_argument(
        "--preflight-report",
        default="build/preflight-report.json",
        help="Path to write preflight JSON report (default: build/preflight-report.json)",
    )
    parser.add_argument(
        "--enable-snapshots",
        action="store_true",
        help="Capture workflow snapshots at key stages",
    )
    parser.add_argument(
        "--snapshot-dir",
        default=None,
        help="Snapshot directory (default: <work-dir>/snapshots)",
    )
    parser.add_argument(
        "--rollback-to-snapshot",
        default=None,
        help="Restore target workspace from the named snapshot and exit",
    )
    parser.add_argument(
        "--enable-diff-report",
        action="store_true",
        help="Generate before/after artifact diff report",
    )
    parser.add_argument(
        "--diff-report",
        default="build/diff-report.json",
        help="Output path for artifact diff report (default: build/diff-report.json)",
    )
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
        "--enable-partition-cache",
        action="store_true",
        help="Enable partition-level caching (disabled by default). APK caching is always enabled.",
    )
    parser.add_argument("--clear-cache", action="store_true", help="Clear all cache before starting")
    parser.add_argument(
        "--show-cache-stats", action="store_true", help="Show cache statistics and exit"
    )
    return parser


def normalize_phases(phases: Sequence[str] | None) -> list[str] | None:
    """Expand and normalize CLI phase arguments."""
    if not phases:
        return None

    expanded: list[str] = []
    for phase in phases:
        expanded.extend(part.strip() for part in phase.split(",") if part.strip())
    return expanded


def _is_remote_input(value: str | None) -> bool:
    """Return whether an input path is a remote URL."""
    if not value:
        return False
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _validate_local_input_path(
    parser: argparse.ArgumentParser, *, value: str | None, label: str
) -> None:
    """Validate local input paths at CLI parse stage for faster failure."""
    if not value or _is_remote_input(value):
        return

    resolved = Path(value).expanduser().resolve()
    if not resolved.exists():
        parser.error(f"--{label} path does not exist: {resolved}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)
    args.phases = normalize_phases(args.phases)

    if args.phases:
        invalid = [phase for phase in args.phases if phase not in VALID_PHASES]
        if invalid:
            parser.error(
                f"invalid choice: {', '.join(invalid)} (choose from {', '.join(VALID_PHASES)})"
            )

    _validate_local_input_path(parser, value=args.stock, label="stock")
    _validate_local_input_path(parser, value=args.port, label="port")
    _validate_local_input_path(parser, value=args.eu_bundle, label="eu-bundle")

    return args
