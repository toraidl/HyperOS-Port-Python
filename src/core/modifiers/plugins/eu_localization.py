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
        if not getattr(self.ctx, "is_port_eu_rom", False):
            return False

        # Check if we have a CN stock to extract from
        if self._is_stock_cn():
            return True

        # Check if we have an EU bundle
        if getattr(self.ctx, "eu_bundle", None) is not None:
            return True

        return False

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
            if isinstance(item, dict):
                pkg_name = item.get("package")

            if pkg_name:
                target_apks = self.ctx.syncer.find_apks_by_package(pkg_name, self.ctx.target_dir)
                for target_apk in target_apks:
                    if target_apk.exists():
                        app_dir = target_apk.parent
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
                            self.logger.info(f"Removing conflicting app: {app_dir}")
                            shutil.rmtree(app_dir)
                        else:
                            target_apk.unlink()

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
                rel_to_extracted = src.relative_to(self.ctx.stock.extracted_dir)

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
        for pkg_name in bundle_packages:
            target_apks = self.ctx.syncer.find_apks_by_package(pkg_name, self.ctx.target_dir)

            if target_apks:
                self.logger.info(
                    f"Replacing EU App: {pkg_name} ({len(target_apks)} instance(s) found)"
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
