import json
import os
import time
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from src.core.config_merger import ConfigMerger
from src.core.modifiers.plugin_system import ModifierPlugin, ModifierRegistry


@ModifierRegistry.register
class PropertyModifier(ModifierPlugin):
    """Handles build.prop and other property modifications."""

    name = "property_modifier"
    description = "Apply build.prop modifications and optimizations"
    priority = 25  # Run after file replacements but before feature unlocks

    def __init__(self, context, **kwargs):
        super().__init__(context, **kwargs)
        self.merger = ConfigMerger(self.logger)

        # Custom build info (can be passed from external parameters)
        self.build_user = os.getenv("BUILD_USER", "Bruce")
        self.build_host = os.getenv("BUILD_HOST", "HyperOS-Port")

    def modify(self) -> bool:
        """Execute all property modification logic"""
        self.logger.info("Starting build.prop modifications...")

        try:
            # 1. Global codename and model replacement
            self._global_codename_replacement()

            # 2. Global replacement from config (time, code, fingerprint, etc.)
            self._update_general_info()

            # 3. Screen density (DPI) migration
            self._update_density()

            # 4. Apply specific fixes (Millet, Blur, Cgroup)
            self._apply_specific_fixes()

            # 5. Reconstruct Hardware Props from Base
            self._reconstruct_props()

            self._regenerate_fingerprint()

            self._optimize_core_affinity()

            # 6. Apply Custom Props from props.json (Highest Priority)
            self._apply_custom_props()

            self.logger.info("Build.prop modifications completed.")
            return True
        except Exception as e:
            # Catch-all for top-level modification method to prevent crashes
            self.logger.error(f"Failed to apply property modifications: {e}")
            return False

    def run(self):
        """Backward compatibility for direct calls."""
        return self.modify()

    def _apply_custom_props(self):
        """
        Load and apply custom properties from props.json across hierarchy.
        """
        paths = [
            Path("devices/common"),
            Path(f"devices/{getattr(self.ctx, 'base_chipset_family', 'unknown')}"),
            Path(f"devices/{self.ctx.stock_rom_code}"),
        ]
        valid_paths = [p for p in paths if p.exists()]

        self.logger.debug(f"Checking props.json in paths: {valid_paths}")
        config, report = self.merger.load_and_merge(valid_paths, "props.json")
        if not config:
            self.logger.debug("No custom props.json found to apply.")
            return

        self.logger.info(f"Applying custom properties from: {', '.join(report.loaded_files)}")
        for partition, props in config.items():
            # Find the prop file for this partition
            prop_file = self.ctx.get_target_prop_file(partition)
            if not prop_file:
                self.logger.warning(f"  Target prop file for partition '{partition}' not found.")
                continue

            self.logger.info(
                f"  Applying {len(props)} props to {partition} ({prop_file.relative_to(self.ctx.target_dir)})"
            )
            for key, value in props.items():
                self._update_or_append_prop(prop_file, key, value)

    def _global_codename_replacement(self):
        """
        Replace Port codename/model with Base codename/model globally in all build.prop files.
        """
        self.logger.info("Performing global codename and model replacement...")

        # Source (Port) -> Target (Base)
        replacements = [
            (self.ctx.port_rom_code, self.ctx.stock_rom_code),
        ]

        # Add product model if available
        port_model = self.ctx.port.get_prop("ro.product.model")
        base_model = self.ctx.stock.get_prop("ro.product.model")
        if port_model and base_model and port_model != base_model:
            replacements.append((port_model, base_model))

        for prop_file in self.ctx.target_dir.rglob("build.prop"):
            try:
                content = prop_file.read_text(encoding="utf-8", errors="ignore")
                new_content = content
                for old, new in replacements:
                    if old and new and old != new:
                        new_content = new_content.replace(old, new)

                if content != new_content:
                    prop_file.write_text(new_content, encoding="utf-8")
            except (IOError, OSError) as e:
                self.logger.error(f"Failed to process {prop_file}: {e}")

    def _reconstruct_props(self):
        """
        Reconstruct critical hardware properties from Stock ROM into Port ROM.
        """
        self.logger.info("Reconstructing hardware properties from Base...")

        # Properties to sync from stock to port
        sync_keys = [
            "ro.product.model",
            "ro.product.brand",
            "ro.product.name",
            "ro.product.device",
            "ro.product.manufacturer",
            "ro.build.product",
            "ro.product.marketname",
        ]

        base_props = {}
        for k in sync_keys:
            val = self.ctx.stock.get_prop(k)
            if val:
                base_props[k] = val

        for prop_file in self.ctx.target_dir.rglob("build.prop"):
            try:
                content = prop_file.read_text(encoding="utf-8", errors="ignore")
                modified = False
                for k, v in base_props.items():
                    if f"{k}=" in content:
                        content = re.sub(f"{re.escape(k)}=.*", f"{k}={v}", content)
                        modified = True

                if modified:
                    prop_file.write_text(content, encoding="utf-8")
            except (IOError, OSError):
                pass  # Ignore file access errors during prop reconstruction

    def _update_general_info(self):
        """Modified to load from devices/common/props_global.json"""

        # Generate timestamp
        now = datetime.now(timezone.utc)
        build_date = now.strftime("%a %b %d %H:%M:%S UTC %Y")
        build_utc = str(int(now.timestamp()))

        base_code = self.ctx.stock_rom_code
        rom_version = self.ctx.target_rom_version

        self.logger.debug(f"General Info Update: BaseCode={base_code}, ROMVersion={rom_version}")

        # Load Config
        config_path = Path("devices/common/props_global.json")
        if not config_path.exists():
            self.logger.warning("props_global.json not found, skipping general info update.")
            return

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError, OSError) as e:
            self.logger.error(f"Failed to load props_global.json: {e}")
            return

        # Prepare replacements
        # 1. Common
        replacements = config.get("common", {})

        # 2. EU vs CN
        is_eu = getattr(self.ctx, "is_port_eu_rom", False)
        if is_eu:
            replacements.update(config.get("eu_rom", {}))
        else:
            replacements.update(config.get("cn_rom", {}))

        # Format values (Placeholder replacement)
        fmt_map = {
            "build_date": build_date,
            "build_utc": build_utc,
            "base_code": base_code,
            "rom_version": rom_version,
            "build_user": self.build_user,
            "build_host": self.build_host,
        }

        # Build final key-value map for processing
        final_replacements = {}
        for k, v in replacements.items():
            formatted_val = v.format(**fmt_map)
            # The map expects: "key=": "key=value"
            final_replacements[f"{k}="] = f"{k}={formatted_val}"

        # Iterate all build.prop and modify
        for prop_file in self.ctx.target_dir.rglob("build.prop"):
            lines = []
            with open(prop_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            new_lines = []
            file_changed = False
            for line in lines:
                original_line = line
                line = line.strip()

                # 1. Dictionary replacement logic
                replaced = False
                for prefix, new_val in final_replacements.items():
                    if line.startswith(prefix):
                        if original_line.strip() != new_val:
                            self.logger.debug(f"[{prop_file.name}] Replace: {line} -> {new_val}")
                            new_lines.append(new_val + "\n")
                            file_changed = True
                        else:
                            new_lines.append(original_line)
                        replaced = True
                        break
                if replaced:
                    continue

                # 2. Delete logic
                if line.startswith("ro.miui.density.primaryscale="):
                    self.logger.debug(f"[{prop_file.name}] Remove: {line}")
                    file_changed = True
                    continue

                new_lines.append(original_line)

            # Write back file
            if file_changed:
                self.logger.debug(
                    f"Writing changes to {prop_file.relative_to(self.ctx.target_dir)}"
                )
                with open(prop_file, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)

    def _update_density(self):
        """Screen density modification"""
        self.logger.info("Updating screen density...")

        # 1. Get density from base
        base_density = None
        for part in ["product", "system"]:
            val = self.ctx.stock.get_prop("ro.sf.lcd_density")
            if val:
                base_density = val
                break

        if not base_density:
            base_density = "440"
            self.logger.warning(f"Base density not found, defaulting to {base_density}")
        else:
            self.logger.info(f"Found Base density: {base_density}")

        # 2. Modify porting package
        found_in_port = False
        target_props = list(self.ctx.target_dir.rglob("build.prop"))

        for prop_file in target_props:
            content = prop_file.read_text(encoding="utf-8", errors="ignore")
            new_content = content

            # Replace ro.sf.lcd_density
            if "ro.sf.lcd_density=" in content:
                self.logger.debug(
                    f"[{prop_file.name}] Updating ro.sf.lcd_density to {base_density}"
                )
                new_content = re.sub(
                    r"ro\.sf\.lcd_density=.*", f"ro.sf.lcd_density={base_density}", new_content
                )
                found_in_port = True

            # Replace persist.miui.density_v2
            if "persist.miui.density_v2=" in content:
                self.logger.debug(
                    f"[{prop_file.name}] Updating persist.miui.density_v2 to {base_density}"
                )
                new_content = re.sub(
                    r"persist\.miui\.density_v2=.*",
                    f"persist.miui.density_v2={base_density}",
                    new_content,
                )

            if content != new_content:
                prop_file.write_text(new_content, encoding="utf-8")

        # 3. If not found, append to product/etc/build.prop
        if not found_in_port:
            product_prop = self.ctx.target_dir / "product/etc/build.prop"
            self._update_or_append_prop(product_prop, "ro.sf.lcd_density", base_density)

    def _apply_specific_fixes(self):
        """Device-specific fixes (Millet, Blur, Cgroup, etc.)"""
        self.logger.info("Applying device-specific fixes...")

        # --- 1. cust_erofs ---
        product_prop = self.ctx.target_dir / "product/etc/build.prop"
        if product_prop.exists():
            self._update_or_append_prop(product_prop, "ro.miui.cust_erofs", "0")

        # --- 2. Millet Fix ---
        millet_ver = self.ctx.stock.get_prop("ro.millet.netlink")
        if not millet_ver:
            self.logger.warning("ro.millet.netlink not found in base, defaulting to 29")
            millet_ver = "29"
        else:
            self.logger.debug(f"Found base millet version: {millet_ver}")

        self._update_or_append_prop(product_prop, "ro.millet.netlink", millet_ver)

        # --- 3. Blur Fix ---
        self._update_or_append_prop(product_prop, "persist.sys.background_blur_supported", "true")
        self._update_or_append_prop(product_prop, "persist.sys.background_blur_version", "2")

        # --- 4. Vendor Fixes (Cgroup) ---
        vendor_prop = self.ctx.target_dir / "vendor/build.prop"
        if vendor_prop.exists():
            content = vendor_prop.read_text(encoding="utf-8", errors="ignore")
            if "persist.sys.millet.cgroup1" in content and "#persist" not in content:
                self.logger.debug(f"[{vendor_prop.name}] Commenting out persist.sys.millet.cgroup1")
                content = content.replace(
                    "persist.sys.millet.cgroup1", "#persist.sys.millet.cgroup1"
                )
                vendor_prop.write_text(content, encoding="utf-8")

    def _update_or_append_prop(self, file_path: Path, key: str, value: str | None):
        """
        Helper function: update, append or delete property.
        If value is None, the property will be removed.
        """
        if not file_path.exists():
            return

        content = file_path.read_text(encoding="utf-8", errors="ignore")
        pattern = re.compile(f"^{re.escape(key)}=.*$", re.MULTILINE)

        match = pattern.search(content)

        if value is None:
            if match:
                self.logger.debug(f"[{file_path.name}] Delete: {key}")
                new_content = pattern.sub("", content)
                # Clean up potential double newlines
                new_content = re.sub(r"\n\n+", "\n\n", new_content)
                file_path.write_text(new_content.strip() + "\n", encoding="utf-8")
            return

        replacement = f"{key}={value}"

        if match:
            if match.group(0) != replacement:
                self.logger.debug(f"[{file_path.name}] Update: {key} -> {value}")
                new_content = pattern.sub(replacement, content)
                file_path.write_text(new_content, encoding="utf-8")
        else:
            self.logger.debug(f"[{file_path.name}] Append: {key}={value}")
            # Ensure file ends with newline before appending
            if content and not content.endswith("\n"):
                content += "\n"
            new_content = content + f"{replacement}\n"
            file_path.write_text(new_content, encoding="utf-8")

    def _regenerate_fingerprint(self):
        """
        Regenerate ro.build.fingerprint and ro.build.description based on modified properties
        Format: Brand/Name/Device:Release/ID/Incremental:Type/Tags
        """
        self.logger.info("Regenerating build fingerprint...")

        def get_current_prop(key, default=""):
            # Priority: product -> system -> vendor
            for part in ["product", "system", "vendor", "mi_ext"]:
                for prop_file in (self.ctx.target_dir / part).rglob("build.prop"):
                    try:
                        with open(prop_file, "r", errors="ignore") as f:
                            for line in f:
                                if line.strip().startswith(f"{key}="):
                                    return line.split("=", 1)[1].strip()
                    except (IOError, OSError, ValueError, IndexError):
                        pass  # Ignore file access or parse errors when reading props
            return default

        # Read components
        brand = get_current_prop("ro.product.brand", "Xiaomi")
        name = get_current_prop("ro.product.mod_device")
        device = get_current_prop("ro.product.device", "miproduct")
        version = get_current_prop("ro.build.version.release")
        build_id = get_current_prop("ro.build.id")
        incremental = get_current_prop("ro.build.version.incremental")
        build_type = get_current_prop("ro.build.type", "user")
        tags = get_current_prop("ro.build.tags", "release-keys")

        self.logger.debug(
            f"Fingerprint components: Brand={brand}, Name={name}, Device={device}, Ver={version}, ID={build_id}, Inc={incremental}"
        )

        # Construct Fingerprint
        new_fingerprint = (
            f"{brand}/{name}/{device}:{version}/{build_id}/{incremental}:{build_type}/{tags}"
        )
        self.logger.info(f"New Fingerprint: {new_fingerprint}")

        # Construct Description
        new_description = f"{name}-{build_type} {version} {build_id} {incremental} {tags}"
        self.logger.debug(f"New Description: {new_description}")

        # Write to all build.prop files
        replacements = {
            "ro.build.fingerprint=": f"ro.build.fingerprint={new_fingerprint}",
            "ro.bootimage.build.fingerprint=": f"ro.bootimage.build.fingerprint={new_fingerprint}",
            "ro.system.build.fingerprint=": f"ro.system.build.fingerprint={new_fingerprint}",
            "ro.product.build.fingerprint=": f"ro.product.build.fingerprint={new_fingerprint}",
            "ro.system_ext.build.fingerprint=": f"ro.system_ext.build.fingerprint={new_fingerprint}",
            "ro.vendor.build.fingerprint=": f"ro.vendor.build.fingerprint={new_fingerprint}",
            "ro.odm.build.fingerprint=": f"ro.odm.build.fingerprint={new_fingerprint}",
            "ro.build.description=": f"ro.build.description={new_description}",
            "ro.system.build.description=": f"ro.system.build.description={new_description}",
        }

        for prop_file in self.ctx.target_dir.rglob("build.prop"):
            lines = []
            try:
                with open(prop_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except (IOError, OSError):
                continue  # Skip files that cannot be read

            new_lines = []
            file_changed = False
            for line in lines:
                original = line
                line = line.strip()
                replaced = False
                for prefix, new_val in replacements.items():
                    if line.startswith(prefix):
                        if original.strip() != new_val:
                            new_lines.append(new_val + "\n")
                            file_changed = True
                        else:
                            new_lines.append(original)
                        replaced = True
                        break
                if not replaced:
                    new_lines.append(original)

            if file_changed:
                self.logger.debug(
                    f"Updated fingerprint in {prop_file.relative_to(self.ctx.target_dir)}"
                )
                with open(prop_file, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)

    def _optimize_core_affinity(self):
        """
        Core allocation and scheduler optimization (supports sm8250, sm8450, sm8550 and Android version differences)
        Updated to use devices/common/scheduler.json
        """
        self.logger.info("Optimizing core affinity and scheduler...")

        product_prop = self.ctx.target_dir / "product/etc/build.prop"
        if not product_prop.exists():
            self.logger.warning(
                "product/etc/build.prop not found, skipping core affinity optimization."
            )
            return

        # 1. Helper function: detect platform code
        def get_platform_code():
            vendor_prop = self.ctx.target_dir / "vendor/build.prop"
            if vendor_prop.exists():
                try:
                    content = vendor_prop.read_text(encoding="utf-8", errors="ignore")
                    if "sm8550" in content:
                        return "sm8550"
                    if "sm8450" in content:
                        return "sm8450"
                    if "sm8250" in content:
                        return "sm8250"
                except (IOError, OSError):
                    pass  # Ignore file access errors when detecting platform
            return "unknown"

        # 2. Load Configuration
        config_path = Path("devices/common/scheduler.json")
        if not config_path.exists():
            self.logger.warning("scheduler.json not found, using empty config.")
            config = {}
        else:
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
            except (json.JSONDecodeError, IOError, OSError) as e:
                self.logger.error(f"Failed to load scheduler.json: {e}")
                return

        # 3. Get state
        platform = get_platform_code()
        android_ver = str(self.ctx.port_android_version)

        self.logger.info(
            f"Applying scheduling for Platform: [{platform}], Android: [{android_ver}]"
        )

        # 4. Match logic
        target_props = {}

        if platform in config:
            target_props = config[platform]
        elif platform == "unknown" and android_ver == "15" and "android_15" in config:
            target_props = config["android_15"]
        else:
            target_props = config.get("default", {})

        # 5. Batch apply
        if target_props:
            self.logger.debug(f"Applying {len(target_props)} scheduling properties...")
            for key, value in target_props.items():
                self._update_or_append_prop(product_prop, key, value)
