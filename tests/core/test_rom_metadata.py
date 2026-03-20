import logging
from pathlib import Path
from types import SimpleNamespace

from src.core.rom_metadata import populate_rom_metadata


def make_mock_rom(tmp_path: Path, name: str, props: dict[str, str]):
    rom = SimpleNamespace()
    rom.extracted_dir = tmp_path / name
    rom.extracted_dir.mkdir(parents=True, exist_ok=True)
    rom.path = tmp_path / f"{name}.zip"
    rom.path.touch()

    def get_prop(key: str, default=None):
        return props.get(key, default)

    rom.get_prop = get_prop
    return rom


def test_populate_rom_metadata_distinguishes_global_and_eu_ports(tmp_path):
    stock_xml = tmp_path / "stock" / "product/etc/device_features"
    stock_xml.mkdir(parents=True)
    (stock_xml / "fuxi.xml").write_text("<xml />")

    stock = make_mock_rom(
        tmp_path,
        "stock",
        {
            "ro.system.build.version.release": "14",
            "ro.vendor.build.version.sdk": "34",
            "ro.vendor.build.version.incremental": "1.0.5.0.UMCCNXM",
            "ro.build.ab_update": "true",
            "ro.build.version.security_patch": "2025-01-01",
        },
    )
    port = make_mock_rom(
        tmp_path,
        "port",
        {
            "ro.system.build.version.release": "15",
            "ro.system.build.version.sdk": "35",
            "ro.mi.os.version.incremental": "2.0.1.0.VNBCNXM",
            "ro.product.product.name": "vermeer",
            "ro.product.mod_device": "xiaomi_global",
            "ro.build.version.security_patch": "2025-02-01",
        },
    )

    ctx = SimpleNamespace(
        stock=stock,
        port=port,
        is_official_modify=False,
        logger=logging.getLogger("test"),
    )

    populate_rom_metadata(ctx)

    assert ctx.stock_rom_code == "fuxi"
    assert ctx.port_rom_code == "vermeer"
    assert ctx.target_rom_version == "2.0.1.0.VMCCNXM"
    assert ctx.is_ab_device is True
    assert ctx.is_port_global_rom is True
    assert ctx.is_port_eu_rom is False
    assert ctx.port_global_region == "global"
    assert ctx.security_patch == "2025-02-01"


def test_populate_rom_metadata_keeps_dev_rom_version_for_dev_builds(tmp_path):
    stock = make_mock_rom(
        tmp_path,
        "stock",
        {
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
            "ro.vendor.build.version.incremental": "1.0.5.0.UMCCNXM",
            "ro.product.vendor.device": "fuxi",
        },
    )
    port = make_mock_rom(
        tmp_path,
        "port",
        {
            "ro.build.version.release": "16",
            "ro.build.version.sdk": "36",
            "ro.mi.os.version.incremental": "2.0.0.0.DEV.VNBCNXM",
            "ro.product.product.name": "vermeer",
        },
    )

    ctx = SimpleNamespace(
        stock=stock,
        port=port,
        is_official_modify=False,
        logger=logging.getLogger("test"),
    )

    populate_rom_metadata(ctx)

    assert ctx.target_rom_version == "2.0.0.0.DEV.VNBCNXM"


def test_populate_rom_metadata_treats_xiaomi_eu_mod_device_as_eu_not_global(tmp_path):
    stock = make_mock_rom(
        tmp_path,
        "stock",
        {
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
            "ro.vendor.build.version.incremental": "1.0.5.0.UMCCNXM",
            "ro.product.vendor.device": "fuxi",
        },
    )
    port = make_mock_rom(
        tmp_path,
        "port",
        {
            "ro.build.version.release": "15",
            "ro.build.version.sdk": "35",
            "ro.mi.os.version.incremental": "2.0.1.0.VNBCNXM",
            "ro.product.product.name": "vermeer",
            "ro.product.mod_device": "xiaomi.eu_vermeer_global",
        },
    )

    ctx = SimpleNamespace(
        stock=stock,
        port=port,
        is_official_modify=False,
        logger=logging.getLogger("test"),
    )

    populate_rom_metadata(ctx)

    assert ctx.is_port_eu_rom is True
    assert ctx.is_port_global_rom is False
    assert ctx.port_global_region == ""


def test_populate_rom_metadata_detects_global_region_variants(tmp_path):
    stock = make_mock_rom(
        tmp_path,
        "stock",
        {
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
            "ro.vendor.build.version.incremental": "1.0.5.0.UMCCNXM",
            "ro.product.vendor.device": "fuxi",
        },
    )

    cases = (
        ("pudding_global", "global"),
        ("pudding_eea_global", "eea"),
        ("pudding_ru_global", "ru"),
        ("pudding_in_global", "in"),
        ("pudding_id_global", "id"),
        ("pudding_tr_global", "tr"),
        ("pudding_lm_cr_global", "lm_cr"),
        ("pudding_tw_global", "tw"),
    )

    for mod_device, expected_region in cases:
        port = make_mock_rom(
            tmp_path,
            f"port_{expected_region}",
            {
                "ro.build.version.release": "15",
                "ro.build.version.sdk": "35",
                "ro.mi.os.version.incremental": "2.0.1.0.VNBCNXM",
                "ro.product.product.name": "vermeer",
                "ro.product.mod_device": mod_device,
            },
        )

        ctx = SimpleNamespace(
            stock=stock,
            port=port,
            is_official_modify=False,
            logger=logging.getLogger("test"),
        )

        populate_rom_metadata(ctx)

        assert ctx.is_port_global_rom is True
        assert ctx.is_port_eu_rom is False
        assert ctx.port_global_region == expected_region


def test_populate_rom_metadata_detects_stock_region_variants(tmp_path):
    cases = (
        (
            {
                "ro.product.mod_device": "pudding_eea_global",
                "ro.miui.build.region": "",
            },
            "eea",
        ),
        (
            {
                "ro.product.mod_device": "pudding_ru_global",
                "ro.miui.build.region": "",
            },
            "ru",
        ),
        (
            {
                "ro.product.mod_device": "pudding_tw_global",
                "ro.miui.build.region": "",
            },
            "tw",
        ),
        (
            {
                "ro.product.mod_device": "pudding",
                "ro.miui.build.region": "",
            },
            "cn",
        ),
        (
            {
                "ro.product.mod_device": "",
                "ro.miui.build.region": "cn",
            },
            "cn",
        ),
    )

    for idx, (stock_extra_props, expected_region) in enumerate(cases):
        stock = make_mock_rom(
            tmp_path,
            f"stock_case_{idx}",
            {
                "ro.build.version.release": "14",
                "ro.build.version.sdk": "34",
                "ro.vendor.build.version.incremental": "1.0.5.0.UMCCNXM",
                "ro.product.vendor.device": "fuxi",
                **stock_extra_props,
            },
        )
        port = make_mock_rom(
            tmp_path,
            f"port_case_{idx}",
            {
                "ro.build.version.release": "15",
                "ro.build.version.sdk": "35",
                "ro.mi.os.version.incremental": "2.0.1.0.VNBCNXM",
                "ro.product.product.name": "vermeer",
                "ro.product.mod_device": "pudding_global",
            },
        )

        ctx = SimpleNamespace(
            stock=stock,
            port=port,
            is_official_modify=False,
            logger=logging.getLogger("test"),
        )

        populate_rom_metadata(ctx)

        assert ctx.stock_region == expected_region
