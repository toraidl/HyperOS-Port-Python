"""Utilities for calling payload-dumper with --json and --metadata options.

This module provides a Python interface to the payload-dumper CLI tool,
enabling extraction of partition metadata and device information from
OTA payload.bin files.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("payload_dumper")


@dataclass
class PartitionInfo:
    """Represents information about a single partition."""

    name: str
    size: int
    hash: str
    old_size: Optional[int] = None
    old_hash: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PartitionInfo":
        """Create PartitionInfo from dictionary."""
        return cls(
            name=data["name"],
            size=data["size"],
            hash=data["hash"],
            old_size=data.get("old_size"),
            old_hash=data.get("old_hash"),
        )


@dataclass
class DynamicPartitionGroup:
    """Represents a dynamic partition group (e.g., qti_dynamic_partitions)."""

    name: str
    size: int
    partition_names: List[str]
    logical_partitions: List[PartitionInfo] = field(default_factory=list)
    total_used: int = 0
    free_space: int = 0
    usage_percent: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DynamicPartitionGroup":
        """Create DynamicPartitionGroup from dictionary."""
        logical_parts = [PartitionInfo.from_dict(p) for p in data.get("logical_partitions", [])]
        return cls(
            name=data["name"],
            size=data["size"],
            partition_names=data.get("partition_names", []),
            logical_partitions=logical_parts,
            total_used=data.get("total_used", 0),
            free_space=data.get("free_space", 0),
            usage_percent=data.get("usage_percent", 0.0),
        )


@dataclass
class PayloadInfo:
    """Represents payload.bin header information."""

    block_size: int = 4096
    minor_version: int = 0
    security_patch_level: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PayloadInfo":
        """Create PayloadInfo from dictionary."""
        return cls(
            block_size=data.get("block_size", 4096),
            minor_version=data.get("minor_version", 0),
            security_patch_level=data.get("security_patch_level"),
        )


@dataclass
class DynamicPartitionMetadata:
    """Represents Virtual A/B compression metadata from payload.bin."""

    cow_version: int = 2
    compression_factor: int = 65536
    snapshot_enabled: bool = True
    vabc_enabled: bool = False
    vabc_compression_param: str = "lz4"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DynamicPartitionMetadata":
        """Create DynamicPartitionMetadata from dictionary."""
        return cls(
            cow_version=data.get("cow_version", 2),
            compression_factor=data.get("compression_factor", 65536),
            snapshot_enabled=data.get("snapshot_enabled", True),
            vabc_enabled=data.get("vabc_enabled", False),
            vabc_compression_param=data.get("vabc_compression_param", "lz4"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "cow_version": self.cow_version,
            "compression_factor": self.compression_factor,
            "snapshot_enabled": self.snapshot_enabled,
            "vabc_enabled": self.vabc_enabled,
            "vabc_compression_param": self.vabc_compression_param,
        }


@dataclass
class PayloadDumperOutput:
    """Represents parsed payload-dumper output with partitions and metadata."""

    payload_info: PayloadInfo = field(default_factory=lambda: PayloadInfo())
    dynamic_partition_groups: List[DynamicPartitionGroup] = field(default_factory=list)
    all_partitions: List[PartitionInfo] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
    dynamic_partition_metadata: Optional[DynamicPartitionMetadata] = None

    @property
    def partition_names(self) -> List[str]:
        """Return list of all partition names."""
        return [p.name for p in self.all_partitions]

    @property
    def logical_partition_names(self) -> List[str]:
        """Return only logical partition names (part of dynamic partition groups)."""
        logical: set[str] = set()
        for group in self.dynamic_partition_groups:
            logical.update(group.partition_names)
        return sorted(list(logical))

    @property
    def firmware_partition_names(self) -> List[str]:
        """Return firmware partition names (not part of dynamic partition groups)."""
        logical = set(self.logical_partition_names)
        return [p.name for p in self.all_partitions if p.name not in logical]

    @property
    def device_code(self) -> Optional[str]:
        """Return device code from metadata (pre-device field)."""
        # Try multiple possible metadata keys
        for key in ["pre-device", "post-build", "device"]:
            value = self.metadata.get(key)
            if value:
                if key == "post-build":
                    # Extract device from build fingerprint
                    # Format: brand/device/device:...
                    parts = value.split("/")
                    if len(parts) >= 2:
                        return parts[1].lower()
                return value.lower()
        return None

    @property
    def security_patch_level(self) -> Optional[str]:
        """Return security patch level from metadata."""
        return self.metadata.get("post-security-patch-level")

    @property
    def build_incremental(self) -> Optional[str]:
        """Return build incremental version from metadata."""
        return self.metadata.get("post-build-incremental")

    @property
    def sdk_level(self) -> Optional[str]:
        """Return SDK level from metadata."""
        return self.metadata.get("post-sdk-level")

    def get_partition(self, name: str) -> Optional[PartitionInfo]:
        """Get partition info by name."""
        for p in self.all_partitions:
            if p.name == name:
                return p
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: Dict[str, Any] = {
            "payload_info": {
                "block_size": self.payload_info.block_size,
                "minor_version": self.payload_info.minor_version,
                "security_patch_level": self.payload_info.security_patch_level,
            },
            "dynamic_partition_groups": [
                {
                    "name": g.name,
                    "size": g.size,
                    "partition_names": g.partition_names,
                    "logical_partitions": [
                        {"name": p.name, "size": p.size, "hash": p.hash}
                        for p in g.logical_partitions
                    ],
                    "total_used": g.total_used,
                    "free_space": g.free_space,
                    "usage_percent": g.usage_percent,
                }
                for g in self.dynamic_partition_groups
            ],
            "all_partitions": [
                {"name": p.name, "size": p.size, "hash": p.hash} for p in self.all_partitions
            ],
            "metadata": self.metadata,
        }
        if self.dynamic_partition_metadata is not None:
            result["dynamic_partition_metadata"] = self.dynamic_partition_metadata.to_dict()
        return result


class PayloadDumperRunner:
    """Runner for payload-dumper CLI tool with --json and --metadata support."""

    def __init__(self, payload_path: Path, timeout: int = 300):
        """Initialize runner.

        Args:
            payload_path: Path to the ROM zip or payload.bin file
            timeout: Command timeout in seconds
        """
        self.payload_path = Path(payload_path)
        self.timeout = timeout
        self._json_output: Optional[Dict[str, Any]] = None
        self._metadata_output: Optional[Dict[str, str]] = None

    def _get_payload_dumper_path(self) -> str:
        """Get path to payload-dumper binary.

        Priority:
        1. Project bin directory (platform-specific)
        2. System PATH
        """
        import platform
        import sys

        # Determine platform and architecture
        system = sys.platform
        machine = platform.machine().lower()

        # Map to project bin directory structure
        if system.startswith("linux"):
            platform_dir = "linux"
        elif system == "darwin":
            platform_dir = "darwin"
        elif system.startswith("win"):
            platform_dir = "windows"
        else:
            platform_dir = system

        # Map architecture
        if machine in ("x86_64", "amd64"):
            arch_dir = "x86_64"
        elif machine in ("arm64", "aarch64"):
            arch_dir = "arm64"
        else:
            arch_dir = machine

        # Try project bin directory first
        project_root = Path(__file__).parent.parent.parent
        project_bin = project_root / "bin" / platform_dir / arch_dir / "payload-dumper"

        # On Windows, add .exe extension
        if system.startswith("win"):
            project_bin = project_bin.with_suffix(".exe")

        if project_bin.exists():
            return str(project_bin)

        # Fall back to PATH
        return "payload-dumper"

    def _run_command(self, *args: str) -> str:
        """Run payload-dumper command and return stdout.

        Args:
            *args: Additional arguments to pass to payload-dumper

        Returns:
            Command stdout as string

        Raises:
            subprocess.CalledProcessError: If command fails
            subprocess.TimeoutExpired: If command times out
            FileNotFoundError: If payload-dumper is not found
        """
        dumper_path = self._get_payload_dumper_path()
        cmd = [dumper_path] + list(args) + [str(self.payload_path)]
        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"payload-dumper failed with exit code {e.returncode}")
            logger.error(f"stderr: {e.stderr}")
            raise
        except subprocess.TimeoutExpired:
            logger.error(f"payload-dumper timed out after {self.timeout}s")
            raise
        except FileNotFoundError:
            logger.error("payload-dumper not found. Please ensure payload-dumper is installed.")
            raise

    def get_partitions_json(self) -> Dict[str, Any]:
        """Get partition list as JSON using --json flag.

        Returns:
            Parsed JSON output containing partition information

        Raises:
            json.JSONDecodeError: If JSON parsing fails
            subprocess.CalledProcessError: If payload-dumper fails
        """
        if self._json_output is not None:
            return self._json_output

        stdout = self._run_command("--json")

        try:
            parsed: Dict[str, Any] = json.loads(stdout)
            self._json_output = parsed
            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON output: {e}")
            if "stdout" in locals():
                logger.error(f"Raw output: {stdout[:500]}...")
            raise

    def get_metadata(self) -> Dict[str, str]:
        """Get metadata as key-value pairs using --metadata flag.

        Returns:
            Dictionary of metadata key-value pairs

        Raises:
            subprocess.CalledProcessError: If payload-dumper fails
        """
        if self._metadata_output is not None:
            return self._metadata_output

        stdout = self._run_command("--metadata")

        metadata: Dict[str, str] = {}
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if "=" in line:
                key, value = line.split("=", 1)
                metadata[key.strip()] = value.strip()

        self._metadata_output = metadata
        return metadata

    def get_full_info(self) -> PayloadDumperOutput:
        """Get combined partition and metadata info.

        This method calls both --json and --metadata and combines the results.

        Returns:
            PayloadDumperOutput with complete information

        Raises:
            subprocess.CalledProcessError: If either command fails
            json.JSONDecodeError: If JSON parsing fails
        """
        json_data = self.get_partitions_json()
        metadata = self.get_metadata()

        output = PayloadDumperOutput()

        # Parse payload info
        if "payload_info" in json_data:
            output.payload_info = PayloadInfo.from_dict(json_data["payload_info"])

        # Parse dynamic partition groups
        if "dynamic_partition_groups" in json_data:
            output.dynamic_partition_groups = [
                DynamicPartitionGroup.from_dict(g) for g in json_data["dynamic_partition_groups"]
            ]

        # Parse all partitions
        if "all_partitions" in json_data:
            output.all_partitions = [
                PartitionInfo.from_dict(p) for p in json_data["all_partitions"]
            ]
        elif "partitions" in json_data:
            # Fallback for older format
            output.all_partitions = [PartitionInfo.from_dict(p) for p in json_data["partitions"]]

        # Parse dynamic partition metadata (VABC settings)
        if "dynamic_partition_metadata" in json_data:
            output.dynamic_partition_metadata = DynamicPartitionMetadata.from_dict(
                json_data["dynamic_partition_metadata"]
            )

        output.metadata = metadata

        return output


def extract_device_info(
    payload_path: Path, fallback_device_code: Optional[str] = None
) -> tuple[str, PayloadDumperOutput]:
    """Extract device code and partition info from ROM.

    This is a convenience function that extracts device information
    from a ROM payload.bin file.

    Args:
        payload_path: Path to the ROM zip file
        fallback_device_code: Device code to use if extraction fails

    Returns:
        Tuple of (device_code, payload_info)

    Raises:
        RuntimeError: If extraction fails and no fallback provided
    """
    runner = PayloadDumperRunner(payload_path)

    try:
        info = runner.get_full_info()
        device_code = info.device_code or fallback_device_code
        if not device_code:
            raise RuntimeError("Could not extract device code from ROM metadata")
        return device_code, info
    except Exception as e:
        logger.warning(f"Failed to extract device info: {e}")
        if fallback_device_code:
            logger.info(f"Using fallback device code: {fallback_device_code}")
            # Return empty output with fallback code
            return fallback_device_code, PayloadDumperOutput()
        raise RuntimeError(f"Failed to extract device info and no fallback provided: {e}") from e


def get_partition_list_from_payload(payload_path: Path) -> List[str]:
    """Get list of partition names from payload.bin.

    Args:
        payload_path: Path to the ROM zip file

    Returns:
        List of partition names

    Raises:
        subprocess.CalledProcessError: If payload-dumper fails
        json.JSONDecodeError: If JSON parsing fails
    """
    runner = PayloadDumperRunner(payload_path)
    info = runner.get_full_info()
    return info.partition_names
