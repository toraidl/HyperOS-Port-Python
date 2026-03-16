"""Tool resolution helpers for the porting workflow."""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


@dataclass
class ResolvedTooling:
    """Resolved tool locations for the current host platform."""

    platform_bin_dir: Path
    tools: SimpleNamespace


def resolve_tooling(project_root: Path, logger: logging.Logger) -> ResolvedTooling:
    """Resolve platform-specific binaries and shared tool locations."""
    bin_root = project_root / "bin"
    system = platform.system().lower()
    machine = platform.machine().lower()

    if machine in ["amd64", "x86_64"]:
        arch = "x86_64"
    elif machine in ["aarch64", "arm64"]:
        arch = "arm64"
    else:
        arch = "x86_64"

    if system == "windows":
        platform_dir = "windows"
        executable_extension = ".exe"
    elif system == "linux":
        platform_dir = "linux"
        executable_extension = ""
    elif system == "darwin":
        platform_dir = "macos"
        executable_extension = ""
    else:
        logger.warning(f"Unknown system: {system}, defaulting to Linux.")
        platform_dir = "linux"
        executable_extension = ""

    platform_bin_dir = bin_root / platform_dir / arch
    fallback_dir = bin_root / platform_dir
    if not platform_bin_dir.exists() and fallback_dir.exists():
        platform_bin_dir = fallback_dir

    logger.info(f"Platform Binary Dir: {platform_bin_dir}")

    tools = SimpleNamespace()
    tools.magiskboot = platform_bin_dir / f"magiskboot{executable_extension}"
    tools.aapt2 = platform_bin_dir / f"aapt2{executable_extension}"
    tools.apktool_jar = bin_root / "apktool" / "apktool_2.12.1.jar"
    tools.apkeditor_jar = bin_root / "APKEditor.jar"

    if not tools.magiskboot.exists():
        logger.warning(f"magiskboot not found at {tools.magiskboot}")

    return ResolvedTooling(platform_bin_dir=platform_bin_dir, tools=tools)
