"""EU Localization plugin.

This plugin applies EU localization bundle to the target ROM.
Supports two modes:
1. Direct extraction from CN stock ROM (when stock is CN)
2. Bundle-based application (when stock is not CN)
"""

import json
import shutil
import tempfile
import zipfile
import subprocess
import re
from pathlib import Path
from typing import Any, Dict, List, Union

from src.core.modifiers.plugin_system import ModifierPlugin, ModifierRegistry


@ModifierRegistry.register
class EULocalizationPlugin(ModifierPlugin):
    """Plugin to apply EU localization bundle or extract from CN stock."""

    name = "eu_localization"
    description = "Apply EU localization from CN stock or bundle to target ROM"
    priority = 50
    dependencies = ["wild_boost"]  # Run after wild_boost

    def check_prerequisites(self) -> bool:
        """Check if EU localization can be applied."""
        # Check if we have an EU bundle (Explicit override)
        if getattr(self.ctx, "eu_bundle", None) is not None:
            return True

        if not getattr(self.ctx, "is_port_eu_rom", False):
            return False

        # Check if we have a CN stock to extract from
        if self._is_stock_cn():
            return True

        return False

    def _get_apk_version(self, apk_path: Path) -> str:
        """Get APK version name/code using aapt2."""
        aapt2 = getattr(getattr(self.ctx, "tools", None), "aapt2", None)
        if not aapt2 or not apk_path.exists():
            return "unknown"

        try:
            cmd = [str(aapt2), "dump", "badging", str(apk_path)]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            match = re.search(r"versionName='([^']*)'", result.stdout)
            version_name = match.group(1) if match else "unknown"
            
            match_code = re.search(r"versionCode='([^']*)'", result.stdout)
            version_code = match_code.group(1) if match_code else "unknown"
            
            return f"{version_name} ({version_code})"
        except Exception:
            return "unknown"

    def _is_stock_cn(self) -> bool:
        """Detect if stock ROM is a CN (China) ROM.

        Uses multiple detection methods:
        1. ro.product.mod_device (most reliable) - CN ROMs don't have _global suffix
        2. ro.miui.build.region - should be 'cn'
        3. ro.product.locale - should be 'zh-CN'
        """
        if not hasattr(self.ctx, "stock") or not self.ctx.stock:
            return False

        stock_props = self.ctx.stock.props

        # Method 1: Check mod_device (most reliable)
        mod_device = stock_props.get("ro.product.mod_device", "").lower()
        if mod_device:
            # If mod_device ends with _global, it's not CN
            if mod_device.endswith("_global"):
                return False
            # If mod_device has no _global, likely CN
            return True

        # Method 2: Check MIUI build region
        region = stock_props.get("ro.miui.build.region", "").lower()
        if region == "cn":
            return True

        # Method 3: Check product locale
        locale = stock_props.get("ro.product.locale", "").lower()
        if locale in ["zh-cn", "zh-hans-cn"]:
            return True

        return False

    def _remove_target_apks(self, target_apks: List[Path]):
        """Remove target APKs and their parent directories."""
        protected_dirs = {
            "app",
            "priv-app",
            "system",
            "product",
            "system_ext",
            "vendor",
            "overlay",
            "framework",
            "mi_ext",
            "odm",
            "oem",
        }

        for target_apk in target_apks:
            if target_apk.exists():
                app_dir = target_apk.parent
                if app_dir.name not in protected_dirs:
                    self.logger.info(f"Removing conflicting app: {app_dir}")
                    shutil.rmtree(app_dir)
                else:
                    target_apk.unlink()

    def modify(self) -> bool:
        """Apply EU localization."""
        # If stock is CN, extract directly from stock
        if self._is_stock_cn():
            self.logger.info("CN stock ROM detected, extracting EU apps directly...")
            return self._extract_from_stock()

        # Otherwise, use bundle
        return self._apply_from_bundle()

    def _load_eu_config(self) -> Dict[str, Any]:
        """Load EU localization extraction config."""
        # Try device-specific config first
        device_code = getattr(self.ctx, "device_code", "common")
        config_paths = [
            Path(f"devices/{device_code}/eu_bundle_config.json"),
            Path("devices/common/eu_bundle_config.json"),
        ]

        for config_path in config_paths:
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    self.logger.warning(f"Failed to load config from {config_path}: {e}")

        return {"apps": []}

    def _extract_from_stock(self) -> bool:
        """Extract EU apps directly from CN stock ROM."""
        config = self._load_eu_config()
        apps_list = config.get("apps", [])

        if not apps_list:
            self.logger.warning("No apps configured for EU localization extraction")
            return False

        self.logger.info(f"Extracting {len(apps_list)} item(s) from CN stock...")

        # First, remove conflicting apps from target
        self.logger.info("Removing conflicting apps from target before extraction...")
        for item in apps_list:
            pkg_name = None
            app_path_str = None

            if isinstance(item, dict):
                pkg_name = item.get("package")
                app_path_str = item.get("path")
            else:
                app_path_str = item

            # Method 1: If package_name provided, use it directly
            if pkg_name:
                target_apks = self.ctx.syncer.find_apks_by_package(pkg_name, self.ctx.target_dir)
                self._remove_target_apks(target_apks)

            # Method 2: If only path provided, find pkg_name from stock first
            elif app_path_str:
                # Find APK in stock to get package_name
                stock_path = self.ctx.stock.extracted_dir / app_path_str
                if stock_path.is_dir():
                    # Find any APK in the directory
                    for apk_file in stock_path.rglob("*.apk"):
                        stock_pkg = self.ctx.syncer._get_apk_package_name(apk_file)
                        if stock_pkg:
                            self.logger.debug(
                                f"Found package {stock_pkg} from stock path {app_path_str}"
                            )
                            # Find and remove all matching APKs in target
                            target_apks = self.ctx.syncer.find_apks_by_package(
                                stock_pkg, self.ctx.target_dir
                            )
                            if target_apks:
                                self.logger.info(
                                    f"Removing conflicting app by path {app_path_str} (package: {stock_pkg})"
                                )
                                self._remove_target_apks(target_apks)
                            break
                elif stock_path.exists() and stock_path.suffix == ".apk":
                    stock_pkg = self.ctx.syncer._get_apk_package_name(stock_path)
                    if stock_pkg:
                        target_apks = self.ctx.syncer.find_apks_by_package(
                            stock_pkg, self.ctx.target_dir
                        )
                        if target_apks:
                            self.logger.info(
                                f"Removing conflicting app by path {app_path_str} (package: {stock_pkg})"
                            )
                            self._remove_target_apks(target_apks)

        extracted_count = 0
        extracted_items: List[Path] = []

        for item in apps_list:
            # Handle both string (legacy) and dict (extended) config
            if isinstance(item, str):
                app_path_str = item
                pkg_name = None
            else:
                app_path_str = item.get("path")
                pkg_name = item.get("package")

            found_srcs: List[Path] = []

            # Method 1: Try package-based lookup first if provided
            if pkg_name:
                matches = self.ctx.syncer.find_apks_by_package(
                    pkg_name, self.ctx.stock.extracted_dir
                )
                if matches:
                    self.logger.debug(f"Found package {pkg_name} at {len(matches)} location(s)")
                    for apk_path in matches:
                        parent = apk_path.parent
                        protected_dirs = {
                            "app",
                            "priv-app",
                            "data-app",
                            "overlay",
                            "framework",
                        }
                        if parent.name not in protected_dirs:
                            found_srcs.append(parent)
                        else:
                            found_srcs.append(apk_path)

            # Method 2: Try path-based lookup if no package found
            if not found_srcs and app_path_str:
                parts = Path(app_path_str).parts
                if parts:
                    partition = parts[0]
                    relative_path = Path(*parts[1:])

                    candidates = [
                        self.ctx.stock.extracted_dir / app_path_str,
                        self.ctx.stock.extracted_dir / partition / partition / relative_path,
                    ]

                    for candidate in candidates:
                        if candidate.exists():
                            found_srcs.append(candidate)
                            break

            if not found_srcs:
                self.logger.warning(f"App not found in CN stock: {item}")
                continue

            # Copy found sources to target
            for src in found_srcs:
                # Validate src is within stock extracted_dir
                try:
                    rel_to_extracted = src.relative_to(self.ctx.stock.extracted_dir)
                except ValueError:
                    self.logger.error(f"Path {src} is not in stock extracted dir, skipping")
                    continue

                # Handle SAR (System-as-Root) double-folder structure
                path_parts = list(rel_to_extracted.parts)
                if len(path_parts) > 1 and path_parts[0] == path_parts[1]:
                    path_parts.pop(0)

                dest_path = self.ctx.target_dir / Path(*path_parts)

                try:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    if src.is_dir():
                        if dest_path.exists():
                            shutil.rmtree(dest_path)
                        shutil.copytree(src, dest_path, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dest_path)

                    self.logger.info(f"Extracted: {Path(*path_parts)}")
                    extracted_items.append(dest_path)
                    extracted_count += 1
                except Exception as e:
                    self.logger.error(f"Failed to copy {src} to {dest_path}: {e}")

        if extracted_count == 0:
            self.logger.warning("No apps extracted from CN stock")
            return False

        self.logger.info(f"Successfully extracted {extracted_count} item(s) from CN stock")

        return True

    def _apply_from_bundle(self) -> bool:
        """Apply EU localization from pre-generated bundle."""
        bundle_path = Path(self.ctx.eu_bundle)
        if not bundle_path.exists():
            self.logger.warning(f"EU Bundle not found at {bundle_path}")
            return False

        self.logger.info(f"Applying EU Localization Bundle from {bundle_path}...")

        with tempfile.TemporaryDirectory(prefix="eu_bundle_") as tmp_dir:
            tmp_path = Path(tmp_dir)

            try:
                with zipfile.ZipFile(bundle_path, "r") as z:
                    z.extractall(tmp_path)
            except Exception as e:
                self.logger.error(f"Failed to extract EU bundle: {e}")
                return False

            # Find and replace EU apps
            self._replace_eu_apps(tmp_path)

            # Merge bundle files
            self.logger.info("Merging EU Bundle files into Target ROM...")
            shutil.copytree(tmp_path, self.ctx.target_dir, dirs_exist_ok=True)

        return True

    def _replace_eu_apps(self, bundle_path: Path):
        """Replace existing apps with EU versions."""
        self.logger.info("Scanning for APKs to replace in target ROM...")

        # Clear syncer package cache to ensure fresh lookup in target_dir
        self.ctx.syncer._target_package_cache = {}

        # 1. Identify all unique packages
        bundle_packages: Dict[str, List[Path]] = {}
        for apk_file in bundle_path.rglob("*.apk"):
            pkg_name = self.ctx.syncer._get_apk_package_name(apk_file)
            if pkg_name:
                if pkg_name not in bundle_packages:
                    bundle_packages[pkg_name] = []
                bundle_packages[pkg_name].append(apk_file)

        self.logger.info(f"Found {len(bundle_packages)} unique package(s) to process.")

        # 2. For each unique package, find and remove original app in target ROM
        for pkg_name, bundle_apks in bundle_packages.items():
            target_apks = self.ctx.syncer.find_apks_by_package(pkg_name, self.ctx.target_dir)

            if target_apks:
                # Log version comparison
                target_ver = self._get_apk_version(target_apks[0])
                bundle_ver = self._get_apk_version(bundle_apks[0])
                self.logger.info(
                    f"Replacing EU App: {pkg_name} [Target: {target_ver} -> Bundle: {bundle_ver}]"
                )

                for target_apk in target_apks:
                    if not target_apk.exists():
                        continue

                    app_dir = target_apk.parent
                    self.logger.info(f"  - Found at: {target_apk.relative_to(self.ctx.target_dir)}")

                    # Safety check: avoid deleting root partition dirs
                    protected_dirs = {
                        "app",
                        "priv-app",
                        "system",
                        "product",
                        "system_ext",
                        "vendor",
                        "overlay",
                        "framework",
                        "mi_ext",
                        "odm",
                        "oem",
                    }

                    if app_dir.name not in protected_dirs:
                        self.logger.debug(f"  - Removing directory: {app_dir}")
                        try:
                            shutil.rmtree(app_dir)
                        except Exception as e:
                            self.logger.error(f"  - Failed to remove {app_dir}: {e}")
                    else:
                        self.logger.debug(
                            f"  - Removing single file (protected parent): {target_apk}"
                        )
                        target_apk.unlink()
            else:
                self.logger.debug(f"Adding new EU App: {pkg_name} (no match in target)")
