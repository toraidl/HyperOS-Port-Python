"""Tests for payload_dumper module."""

import json
from pathlib import Path

from src.utils.payload_dumper import (
    DynamicPartitionMetadata,
    PayloadDumperOutput,
)


class TestDynamicPartitionMetadata:
    """Tests for DynamicPartitionMetadata dataclass."""

    def test_from_dict_with_all_fields(self) -> None:
        data = {
            "cow_version": 3,
            "compression_factor": 65536,
            "snapshot_enabled": True,
            "vabc_enabled": True,
            "vabc_compression_param": "lz4",
        }
        metadata = DynamicPartitionMetadata.from_dict(data)
        assert metadata.cow_version == 3
        assert metadata.compression_factor == 65536
        assert metadata.snapshot_enabled is True
        assert metadata.vabc_enabled is True
        assert metadata.vabc_compression_param == "lz4"

    def test_from_dict_with_defaults(self) -> None:
        data: dict = {}
        metadata = DynamicPartitionMetadata.from_dict(data)
        assert metadata.cow_version == 2
        assert metadata.compression_factor == 65536
        assert metadata.snapshot_enabled is True
        assert metadata.vabc_enabled is False
        assert metadata.vabc_compression_param == "lz4"

    def test_to_dict(self) -> None:
        metadata = DynamicPartitionMetadata(
            cow_version=3,
            compression_factor=65536,
            snapshot_enabled=True,
            vabc_enabled=True,
            vabc_compression_param="lz4",
        )
        result = metadata.to_dict()
        assert result["cow_version"] == 3
        assert result["compression_factor"] == 65536
        assert result["snapshot_enabled"] is True
        assert result["vabc_enabled"] is True
        assert result["vabc_compression_param"] == "lz4"

    def test_roundtrip(self) -> None:
        original = DynamicPartitionMetadata(
            cow_version=3,
            compression_factor=32768,
            snapshot_enabled=False,
            vabc_enabled=True,
            vabc_compression_param="gz",
        )
        data = original.to_dict()
        restored = DynamicPartitionMetadata.from_dict(data)
        assert restored.cow_version == original.cow_version
        assert restored.compression_factor == original.compression_factor
        assert restored.snapshot_enabled == original.snapshot_enabled
        assert restored.vabc_enabled == original.vabc_enabled
        assert restored.vabc_compression_param == original.vabc_compression_param


class TestPayloadDumperOutput:
    """Tests for PayloadDumperOutput dataclass."""

    def test_to_dict_includes_dynamic_partition_metadata(self) -> None:
        output = PayloadDumperOutput(
            dynamic_partition_metadata=DynamicPartitionMetadata(
                cow_version=3,
                vabc_enabled=True,
            )
        )
        result = output.to_dict()
        assert "dynamic_partition_metadata" in result
        assert result["dynamic_partition_metadata"]["cow_version"] == 3
        assert result["dynamic_partition_metadata"]["vabc_enabled"] is True

    def test_to_dict_without_dynamic_partition_metadata(self) -> None:
        output = PayloadDumperOutput()
        result = output.to_dict()
        assert "dynamic_partition_metadata" not in result

    def test_from_test_json_structure(self) -> None:
        test_json_path = Path(__file__).parent.parent / "test.json"
        if not test_json_path.exists():
            return

        with open(test_json_path, "r") as f:
            data = json.load(f)

        if "dynamic_partition_metadata" not in data:
            return

        metadata = DynamicPartitionMetadata.from_dict(data["dynamic_partition_metadata"])
        assert metadata.cow_version == 3
        assert metadata.compression_factor == 65536
        assert metadata.snapshot_enabled is True
        assert metadata.vabc_enabled is True
        assert metadata.vabc_compression_param == "lz4"
