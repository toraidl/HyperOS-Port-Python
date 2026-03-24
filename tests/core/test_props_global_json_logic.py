import logging
from types import SimpleNamespace

import src.core.modifiers  # noqa: F401
from src.core.props import PropertyModifier


def _write_minimal_props_global_json(tmp_path):
    config_dir = tmp_path / "devices" / "common"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "props_global.json").write_text(
        """{
  "common": {},
  "eu_rom": {
    "ro.product.mod_device": "{base_code}_xiaomieu_global"
  },
  "global_rom": {
    "ro.product.mod_device": "{global_mod_device}"
  },
  "cn_rom": {
    "ro.product.mod_device": "{base_code}"
  }
}
""",
        encoding="utf-8",
    )


def _build_ctx(tmp_path, *, is_port_eu_rom, is_port_global_rom, port_global_region):
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        target_dir=target_dir,
        stock_rom_code="pudding",
        target_rom_version="OS2.0.0.TEST",
        is_port_eu_rom=is_port_eu_rom,
        is_port_global_rom=is_port_global_rom,
        port_global_region=port_global_region,
    )


def _run_update_and_get_mod_device(ctx):
    prop_file = ctx.target_dir / "build.prop"
    prop_file.write_text("ro.product.mod_device=old_value\n", encoding="utf-8")
    modifier = PropertyModifier(ctx, logger=logging.getLogger("test.props.global.json"))
    modifier._update_general_info()
    for line in prop_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("ro.product.mod_device="):
            return line.split("=", 1)[1]
    raise AssertionError("ro.product.mod_device not found")


def test_global_rom_branch_uses_region_suffix(tmp_path, monkeypatch):
    _write_minimal_props_global_json(tmp_path)
    monkeypatch.chdir(tmp_path)
    ctx = _build_ctx(
        tmp_path,
        is_port_eu_rom=False,
        is_port_global_rom=True,
        port_global_region="tw",
    )
    assert _run_update_and_get_mod_device(ctx) == "pudding_tw_global"


def test_global_rom_branch_uses_generic_global_suffix_when_region_missing(tmp_path, monkeypatch):
    _write_minimal_props_global_json(tmp_path)
    monkeypatch.chdir(tmp_path)
    ctx = _build_ctx(
        tmp_path,
        is_port_eu_rom=False,
        is_port_global_rom=True,
        port_global_region="",
    )
    assert _run_update_and_get_mod_device(ctx) == "pudding_global"
