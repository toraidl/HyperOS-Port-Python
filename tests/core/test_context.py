from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.context import PortingContext


def make_mock_rom(tmp_path: Path, name: str):
    rom = MagicMock()
    rom.extracted_dir = tmp_path / name
    rom.extracted_dir.mkdir()
    rom.label = name
    return rom


def test_initialize_target_preserves_existing_root_when_not_requested(tmp_path):
    stock = make_mock_rom(tmp_path, "stock")
    port = make_mock_rom(tmp_path, "port")
    target_dir = tmp_path / "target"
    sentinel = target_dir / "keep.txt"
    target_dir.mkdir()
    sentinel.write_text("preserve")

    context = PortingContext(stock, port, target_dir)

    with (
        patch.object(context, "_install_partition"),
        patch.object(context, "_copy_firmware_images"),
        patch.object(context, "get_rom_info"),
    ):
        context.initialize_target(clean_existing=False)

    assert sentinel.exists()


def test_initialize_target_cleans_existing_root_when_requested(tmp_path):
    stock = make_mock_rom(tmp_path, "stock")
    port = make_mock_rom(tmp_path, "port")
    target_dir = tmp_path / "target"
    sentinel = target_dir / "delete.txt"
    target_dir.mkdir()
    sentinel.write_text("remove")

    context = PortingContext(stock, port, target_dir)

    with (
        patch.object(context, "_install_partition"),
        patch.object(context, "_copy_firmware_images"),
        patch.object(context, "get_rom_info"),
    ):
        context.initialize_target(clean_existing=True)

    assert not sentinel.exists()
