"""Device configuration auto-generation from OTA metadata.

This module provides functionality to automatically create device configuration
files when a new device is detected from ROM metadata.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.config_loader import ConfigMerger
from src.utils.payload_dumper import PayloadDumperOutput, extract_device_info

logger = logging.getLogger("device_auto_config")


# Default configuration templates
DEFAULT_CONFIG_JSON = {
    "_comment": "Auto-generated config - Modify as needed",
    "wild_boost": {"enable": False},
    "pack": {"type": "payload", "fs_type": "erofs"},
    "ksu": {"enable": True},
    "cache": {"partitions": False},
}

DEFAULT_FEATURES_JSON: Dict[str, Any] = {
    "xml_features": {},
    "build_props": {"product": {}},
}

DEFAULT_REPLACEMENTS_JSON: List[Dict[str, Any]] = []

DEFAULT_PROPS_JSON: Dict[str, Dict[str, str]] = {}


class DeviceAutoConfig:
    """Auto-generates device configuration files from ROM metadata."""

    def __init__(
        self,
        device_code: str,
        payload_info: PayloadDumperOutput,
        stock_props: Optional[Dict[str, str]] = None,
    ):
        """Initialize auto-config generator.

        Args:
            device_code: Device codename (e.g., 'fuxi', 'mayfly')
            payload_info: Parsed payload-dumper output with partition info
            stock_props: Optional ROM properties from build.prop
        """
        self.device_code = device_code
        self.payload_info = payload_info
        self.stock_props = stock_props or {}
        self.devices_dir = Path("devices")
        self.config_dir = self.devices_dir / device_code

    def config_exists(self) -> bool:
        """Check if device config already exists."""
        return self.config_dir.exists() and any(self.config_dir.iterdir())

    def create_config_directory(self) -> Path:
        """Create devices/{device_code}/ directory structure.

        Returns:
            Path to created config directory
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created device config directory: {self.config_dir}")
        return self.config_dir

    def generate_config_json(self) -> Path:
        """Generate config.json with default settings.

        Returns:
            Path to generated config file
        """
        config_path = self.config_dir / "config.json"

        # Start with default template
        config = DEFAULT_CONFIG_JSON.copy()
        config["_comment"] = f"Auto-generated config for {self.device_code}"

        # Add partition-specific settings if available
        logical_parts = self.payload_info.logical_partition_names
        if logical_parts:
            config["pack"]["partitions"] = logical_parts

        # Write config file
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

        logger.info(f"Generated config.json: {config_path}")
        return config_path

    def generate_features_json(self) -> Path:
        """Generate features.json with empty features template.

        Returns:
            Path to generated features file
        """
        features_path = self.config_dir / "features.json"

        features = DEFAULT_FEATURES_JSON.copy()

        # Add device-specific build props if available
        product_name = self.stock_props.get("ro.product.product.name", self.device_code)
        if product_name:
            features["build_props"]["product"]["ro.product.spoofed.name"] = product_name

        # Add some common feature placeholders
        features["xml_features"] = {
            "_comment": "Add device-specific features here (e.g., support_AI_display, support_wild_boost)"
        }

        with open(features_path, "w", encoding="utf-8") as f:
            json.dump(features, f, indent=4, ensure_ascii=False)

        logger.info(f"Generated features.json: {features_path}")
        return features_path

    def generate_replacements_json(self) -> Path:
        """Generate replacements.json for file overlays.

        Returns:
            Path to generated replacements file
        """
        replacements_path = self.config_dir / "replacements.json"

        replacements = DEFAULT_REPLACEMENTS_JSON.copy()

        # Add template entry
        replacements.append(
            {
                "_comment": "Add file/directory replacement rules here",
                "description": "Example: Replace system files",
                "type": "file",
                "search_path": "system",
                "files": [],
            }
        )

        with open(replacements_path, "w", encoding="utf-8") as f:
            json.dump(replacements, f, indent=4, ensure_ascii=False)

        logger.info(f"Generated replacements.json: {replacements_path}")
        return replacements_path

    def generate_props_json(self) -> Optional[Path]:
        """Generate props.json from ROM properties.

        Returns:
            Path to generated props file, or None if no relevant props
        """
        if not self.stock_props:
            return None

        props_path = self.config_dir / "props.json"

        # Extract relevant props for porting
        relevant_props: Dict[str, Dict[str, str]] = {}

        # Build fingerprint related props
        build_props = {}
        for key in [
            "ro.product.name_for_attestation",
            "ro.product.vendor.device",
            "ro.product.product.name",
            "ro.product.product.device",
            "ro.product.system.name",
            "ro.product.system.device",
        ]:
            if key in self.stock_props:
                build_props[key] = self.stock_props[key]

        if build_props:
            relevant_props["build"] = build_props

        # Security patch related props
        security_props = {}
        for key in [
            "ro.build.version.security_patch",
            "ro.vendor.build.security_patch",
        ]:
            if key in self.stock_props:
                security_props[key] = self.stock_props[key]

        if security_props:
            relevant_props["security"] = security_props

        if not relevant_props:
            return None

        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(relevant_props, f, indent=4, ensure_ascii=False)

        logger.info(f"Generated props.json: {props_path}")
        return props_path

    def _get_super_size(self) -> int:
        """Get super partition size from payload info or estimate based on device code."""
        if self.payload_info and self.payload_info.dynamic_partition_groups:
            first_group = self.payload_info.dynamic_partition_groups[0]
            super_size = first_group.size
            if super_size:
                logger.info(f"Using super_size from payload: {super_size}")
                return super_size

        device_code = self.device_code.upper()
        size_map = {
            9663676416: ["FUXI", "NUWA", "ISHTAR", "MARBLE", "SOCRATES", "BABYLON"],
            9122611200: ["SUNSTONE"],
            11811160064: ["YUDI"],
            13411287040: ["PANDORA", "POPSICLE", "PUDDING", "NEZHA"],
        }
        for size, devices in size_map.items():
            if device_code in devices:
                return size
        return 9126805504

    def create_partition_info(self) -> Path:
        """Create partition_info.json from payload-dumper output.

        This file stores the partition layout for reference during packing.

        Returns:
            Path to generated partition info file
        """
        info_path = self.config_dir / "partition_info.json"

        # Build dynamic partition info from payload
        dynamic_partitions = []
        super_size = self._get_super_size()

        if self.payload_info and self.payload_info.dynamic_partition_groups:
            group = self.payload_info.dynamic_partition_groups[0]
            dynamic_partitions = group.partition_names
            # Get super_size from payload if available
            if group.size:
                super_size = group.size

        # Build firmware partitions list (all partitions not in dynamic_partitions)
        firmware_partitions = []
        if self.payload_info:
            dynamic_set = set(dynamic_partitions)
            firmware_partitions = [
                p.name for p in self.payload_info.all_partitions if p.name not in dynamic_set
            ]

        partition_info = {
            "device_code": self.device_code,
            "super_size": super_size,
            "dynamic_partitions": dynamic_partitions,
            "firmware_partitions": firmware_partitions,
        }

        # Add dynamic_partition_metadata if available from payload
        if self.payload_info and self.payload_info.dynamic_partition_metadata is not None:
            partition_info["dynamic_partition_metadata"] = (
                self.payload_info.dynamic_partition_metadata.to_dict()
            )

        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(partition_info, f, indent=4, ensure_ascii=False)

        logger.info(f"Generated partition_info.json: {info_path}")
        return info_path

    def setup_device(self) -> Dict[str, Any]:
        """Complete device setup - create all config files.

        This method creates the full device configuration directory structure
        and generates all necessary configuration files.

        Returns:
            Merged device configuration dictionary
        """
        if self.config_exists():
            logger.info(f"Device config already exists for {self.device_code}")
            partition_info_path = self.config_dir / "partition_info.json"
            if not partition_info_path.exists():
                logger.info(
                    "partition_info.json missing for %s, generating it now.",
                    self.device_code,
                )
                self.create_partition_info()

            merger = ConfigMerger(logger)
            return merger.load_device_config(self.device_code)

        logger.info(f"Setting up new device configuration for {self.device_code}")

        # Create directory
        self.create_config_directory()

        # Generate all config files
        self.generate_config_json()
        self.generate_features_json()
        self.generate_replacements_json()
        self.generate_props_json()
        self.create_partition_info()

        # Load and return merged config
        merger = ConfigMerger(logger)
        config = merger.load_device_config(self.device_code)

        logger.info(f"Device configuration setup complete for {self.device_code}")
        return config


def auto_configure_device(
    payload_path: Path,
    fallback_device_code: Optional[str] = None,
    stock_props: Optional[Dict[str, str]] = None,
    logger: Optional[logging.Logger] = None,
    payload_info: Optional[PayloadDumperOutput] = None,
) -> Dict[str, Any]:
    """Main entry point - auto-configure device from payload.bin.

    This function extracts device information from a ROM payload.bin file
    and automatically creates the device configuration directory if it
    doesn't already exist.

    Args:
        payload_path: Path to the ROM zip file containing payload.bin
        fallback_device_code: Fallback device code if metadata extraction fails
        stock_props: Optional ROM properties from build.prop
        logger: Optional logger instance
        payload_info: Optional pre-parsed payload info (avoids re-extraction)

    Returns:
        Merged device configuration dictionary
    """
    if logger:
        global_logger = logging.getLogger("device_auto_config")
        global_logger.handlers = logger.handlers
        global_logger.setLevel(logger.level)

    if payload_info is not None:
        device_code = payload_info.device_code or fallback_device_code
        if not device_code:
            raise RuntimeError("Could not determine device code from payload_info")
    else:
        try:
            device_code, payload_info = extract_device_info(payload_path, fallback_device_code)
        except Exception as e:
            logger = logger or logging.getLogger("device_auto_config")
            logger.warning(f"Failed to extract device info: {e}")
            if fallback_device_code:
                device_code = fallback_device_code
                payload_info = PayloadDumperOutput()
            else:
                raise RuntimeError(f"Could not determine device code: {e}") from e

    auto_config = DeviceAutoConfig(device_code, payload_info, stock_props)
    return auto_config.setup_device()


def get_or_create_device_config(
    device_code: str,
    payload_path: Optional[Path] = None,
    stock_props: Optional[Dict[str, str]] = None,
    logger: Optional[logging.Logger] = None,
    payload_info: Optional[PayloadDumperOutput] = None,
) -> Dict[str, Any]:
    """Get existing config or create from payload if missing.

    This is a convenience function that checks if device configuration
    exists, and if not, attempts to create it from ROM metadata.

    Args:
        device_code: Expected device code
        payload_path: Path to ROM zip (for auto-configuration)
        stock_props: Optional ROM properties
        logger: Optional logger instance
        payload_info: Optional pre-parsed payload info (avoids re-extraction)

    Returns:
        Merged device configuration dictionary
    """
    devices_dir = Path("devices")
    config_dir = devices_dir / device_code

    # If config exists, just load it
    if config_dir.exists() and any(config_dir.iterdir()):
        if logger:
            logger.info(f"Loading existing config for {device_code}")
        partition_info_path = config_dir / "partition_info.json"
        if not partition_info_path.exists():
            if logger:
                logger.info(
                    "partition_info.json missing for %s, auto-generating...",
                    device_code,
                )
            auto_config = DeviceAutoConfig(
                device_code,
                payload_info or PayloadDumperOutput(),
                stock_props,
            )
            auto_config.create_config_directory()
            auto_config.create_partition_info()
        merger = ConfigMerger(logger)
        return merger.load_device_config(device_code)

    # Config doesn't exist, try to auto-create
    if logger:
        logger.info(f"No config found for {device_code}, attempting auto-configuration")

    if not payload_path and payload_info is None:
        raise RuntimeError(
            f"Device config for {device_code} not found and no payload path provided "
            "for auto-configuration"
        )

    return auto_configure_device(
        payload_path or Path("."), device_code, stock_props, logger, payload_info
    )


def update_partition_info(
    device_code: str,
    payload_info: PayloadDumperOutput,
) -> Path:
    """Update partition_info.json for an existing device.

    This can be used to refresh partition information when processing
    new ROM versions for an existing device.

    Args:
        device_code: Device codename
        payload_info: Parsed payload-dumper output

    Returns:
        Path to updated partition info file
    """
    devices_dir = Path("devices")
    config_dir = devices_dir / device_code

    if not config_dir.exists():
        raise FileNotFoundError(f"Device config directory not found: {config_dir}")

    info_path = config_dir / "partition_info.json"

    partition_info = {
        "device_code": device_code,
        "payload_info": payload_info.to_dict(),
        "pack_partitions": payload_info.logical_partition_names,
        "firmware_partitions": payload_info.firmware_partition_names,
    }

    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(partition_info, f, indent=4, ensure_ascii=False)

    logger.info(f"Updated partition_info.json for {device_code}")
    return info_path
