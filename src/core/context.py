from __future__ import annotations

import concurrent.futures
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union, cast

from src.core.rom import RomPackage
from src.core.rom_metadata import populate_rom_metadata
from src.core.tooling import resolve_tooling
from src.core.workspace import (
    build_partition_layout,
    copy_firmware_images,
    install_partition,
    prepare_target_directories,
)
from src.utils.shell import ShellRunner
from src.utils.sync_engine import ROMSyncEngine

if TYPE_CHECKING:
    from src.core.cache_manager import PortRomCacheManager


class PortingContext:
    """Context class for managing ROM porting operations."""

    def __init__(
        self,
        stock_rom: RomPackage,
        port_rom: RomPackage,
        target_work_dir: Union[str, Path],
        is_official_modify: bool = False,
    ) -> None:
        self.stock: RomPackage = stock_rom
        self.port: RomPackage = port_rom
        self.is_official_modify: bool = is_official_modify
        self.project_root: Path = Path(".").resolve()  # Project root
        self.bin_root: Path = self.project_root / "bin"
        self.target_dir: Path = Path(target_work_dir).resolve()  # build/target/
        self.target_config_dir: Path = self.target_dir / "config"
        self.repack_images_dir: Path = self.target_dir / "repack_images"
        self.stock_rom_dir: Path = self.stock.extracted_dir
        self.target_rom_dir: Path = self.target_dir
        self.logger: logging.Logger = logging.getLogger("Context")
        self._init_tools()
        self.syncer: ROMSyncEngine = ROMSyncEngine(self, logging.getLogger("SyncEngine"))
        self.shell: ShellRunner = ShellRunner()
        self.enable_ksu: bool = False
        self.enable_custom_avb_chain: bool = False
        self.avb_key_path: Optional[Path] = None
        self.cache_manager: PortRomCacheManager | None = None
        self.device_config: dict[str, Any] = {}
        self.eu_bundle: str | None = None
        self.base_android_version: str = "0"
        self.port_android_version: str = "0"
        self.base_android_sdk: str = "0"
        self.port_android_sdk: str = "0"
        self.target_rom_version: str = ""
        self.stock_rom_code: str = "unknown"
        self.port_rom_code: str = "unknown"
        self.is_ab_device: bool = False
        self.security_patch: str = "Unknown"
        self.is_port_eu_rom: bool = False
        self.is_port_global_rom: bool = False
        self.port_global_region: str = ""
        self.stock_region: str = ""

    def _init_tools(self) -> None:
        """Resolve platform-specific tooling paths."""
        resolved_tooling = resolve_tooling(self.project_root, self.logger)
        self.platform_bin_dir = resolved_tooling.platform_bin_dir
        self.tools = resolved_tooling.tools

    def initialize_target(self, *, clean_existing: bool = False) -> None:
        """
        Initialize target workspace (Parallel optimized).
        1. Define partition sources (Stock vs Port).
        2. Extract and copy folders.
        3. Copy corresponding SELinux/fs_config configurations.
        """
        self.logger.info(f"Initializing Target Workspace at {self.target_dir}")

        prepare_target_directories(self, clean_existing=clean_existing)
        partition_layout = build_partition_layout(self)

        # Use ThreadPoolExecutor for parallel partition installation
        max_workers: int = 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: List[concurrent.futures.Future[None]] = []
            for part_name, source_rom in partition_layout.items():
                futures.append(executor.submit(self._install_partition, part_name, source_rom))

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Partition install failed: {e}")
                    # raise e # Optional

        self._copy_firmware_images(list(partition_layout))

        self.get_rom_info()

        self.logger.info("Target Workspace Initialized.")

    def _install_partition(self, part_name: str, source_rom: RomPackage) -> None:
        """Install partition from source ROM to target workspace."""
        install_partition(self, part_name, source_rom)

    def _copy_firmware_images(self, exclude_list: List[str]) -> None:
        """Copy firmware images from the stock ROM into the target workspace."""
        copy_firmware_images(self, exclude_list)

    def get_rom_info(self) -> None:
        """Fetch detailed parameters for stock and port ROMs."""
        populate_rom_metadata(self)

    def get_target_prop_file(self, part_name: str) -> Optional[Path]:
        """
        Find build.prop in build/target/{part_name}
        """
        part_dir: Path = self.target_dir / part_name
        if not part_dir.exists():
            return None

        # Check root
        p1: Path = part_dir / "build.prop"
        if p1.exists():
            return p1

        # Check system/build.prop (for system partition)
        p2: Path = part_dir / "system" / "build.prop"
        if p2.exists():
            return p2

        # Check etc/build.prop (for product/odm etc)
        p3: Path = part_dir / "etc" / "build.prop"
        if p3.exists():
            return p3

        # Recursive fallback (slow)
        try:
            return next(part_dir.rglob("build.prop"))
        except StopIteration:
            return None

    # =========================================================================
    # APK Cache Methods
    # =========================================================================

    def build_apk_caches(self, force: bool = False) -> Dict[str, int]:
        """
        Build APK caches for fast lookup.

        Delegates to ROMSyncEngine for caching.

        Args:
            force: If True, rebuild even if caches already exist

        Returns:
            dict with 'files' and 'packages' counts
        """
        # 1. Build name cache
        rom_cache = self.syncer._get_rom_cache(self.target_dir)
        if force or not rom_cache:
            # Force build by passing target_dir
            self.syncer.find_apk_by_name("dummy.apk", self.target_dir)

        # 2. Build package cache
        package_cache = self.syncer._get_package_cache(self.target_dir)
        if force or not package_cache:
            # Force build by passing target_dir
            self.syncer.find_apk_by_package("dummy.package", self.target_dir)

        return cast(dict[str, int], self.syncer.get_apk_cache_stats())

    def _get_apk_package_name(self, apk_path: Path) -> Optional[str]:
        """
        Use aapt2 to parse APK package name.

        Args:
            apk_path: Path to APK file

        Returns:
            Package name string or None if failed
        """
        if not apk_path.exists() or not self.tools.aapt2.exists():
            return None

        cmd: List[str] = [str(self.tools.aapt2), "dump", "packagename", str(apk_path)]
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=5,  # Prevent hanging on corrupt APKs
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            self.logger.debug(f"Failed to parse package name for {apk_path.name}: {e}")
            return None

    def find_apk_by_name(self, apk_name: str) -> Optional[Path]:
        """
        Find APK by filename (case-insensitive).

        Delegates to ROMSyncEngine for caching.

        Args:
            apk_name: APK filename without path (e.g., "Settings" or "settings.apk")

        Returns:
            Path to APK or None if not found
        """
        return cast(Optional[Path], self.syncer.find_apk_by_name(apk_name, self.target_dir))

    def find_apk_by_package(self, package_name: str) -> Optional[Path]:
        """
        Find APK by package name.

        Delegates to ROMSyncEngine for caching.

        Args:
            package_name: Full package name (e.g., "com.android.settings")

        Returns:
            Path to APK or None if not found
        """
        return cast(Optional[Path], self.syncer.find_apk_by_package(package_name, self.target_dir))

    def clear_apk_caches(self) -> None:
        """Clear APK caches to free memory."""
        self.syncer._rom_caches.clear()
        self.syncer._package_caches.clear()
        self.logger.debug("APK caches cleared")
