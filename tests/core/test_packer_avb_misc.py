import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.packer import Repacker, parse_avbtool_info_output


def test_parse_avbtool_info_output_extracts_chain_and_descriptors() -> None:
    output = """
Image size:               1048576 bytes
Original image size:      32 bytes
Algorithm:                SHA256_RSA4096
Rollback Index:           1767225600
Flags:                    1
Descriptors:
    Chain Partition descriptor:
      Partition Name:          boot
      Rollback Index Location: 3
    Chain Partition descriptor:
      Partition Name:          recovery
      Rollback Index Location: 1
    Hash descriptor:
      Partition Name:        dtbo
    Hashtree descriptor:
      Partition Name:        system
    Hashtree descriptor:
      Partition Name:        system_ext
"""
    parsed = parse_avbtool_info_output(output)

    assert parsed["image_size"] == 1048576
    assert parsed["original_image_size"] == 32
    assert parsed["algorithm"] == "SHA256_RSA4096"
    assert parsed["rollback_index"] == 1767225600
    assert parsed["flags"] == 1
    assert parsed["chain_partitions"] == [("boot", 3), ("recovery", 1)]
    assert parsed["hash_partitions"] == ["dtbo"]
    assert parsed["hashtree_partitions"] == ["system", "system_ext"]


def test_sync_partition_info_from_stock_avb(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    device_dir = tmp_path / "devices/pudding"
    device_dir.mkdir(parents=True)
    (device_dir / "partition_info.json").write_text(
        (
            "{\n"
            '  "device_code": "pudding",\n'
            '  "super_size": 13411287040,\n'
            '  "dynamic_partitions": ["system", "vendor"]\n'
            "}\n"
        ),
        encoding="utf-8",
    )
    stock = tmp_path / "build/stockrom/images"
    stock.mkdir(parents=True)
    (stock / "boot.img").write_bytes(b"x")
    (stock / "pvmfw.img").write_bytes(b"x")
    (stock / "vbmeta.img").write_bytes(b"x")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)

    profile = {
        "hash_parts": {"boot", "pvmfw"},
        "hashtree_parts": {"system"},
        "chain_parts": [("boot", 3)],
    }

    def fake_info(_avbtool, image):  # type: ignore[no-untyped-def]
        if image.name == "boot.img":
            return {"image_size": 100663296}
        if image.name == "pvmfw.img":
            return {"image_size": 1048576}
        return {}

    monkeypatch.setattr(repacker, "_run_avbtool_info_image", fake_info)
    repacker._sync_partition_info_from_stock_avb(profile)

    content = (device_dir / "partition_info.json").read_text(encoding="utf-8")
    assert '"boot": 100663296' in content
    assert '"pvmfw": 1048576' in content
    assert '"avb_hash_partitions": [' in content
    assert '"avb_strict_partitions": [' in content


def test_generate_meta_info_includes_avb_lines(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    for name in ("system.img", "vendor.img", "boot.img"):
        (images_out / name).write_bytes(b"x")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)

    monkeypatch.setattr(
        repacker,
        "_build_avb_misc_lines_from_stock",
        lambda _parts: [
            "avb_enable=true",
            "avb_avbtool=avbtool",
            "avb_boot_key_path=/tmp/testkey.pem",
            "avb_boot_algorithm=SHA256_RSA4096",
        ],
    )

    repacker._generate_meta_info()

    misc_info = (tmp_path / "out/target/product/pudding/META/misc_info.txt").read_text(
        encoding="utf-8"
    )
    assert "recovery_api_version=3" in misc_info
    assert "fstab_version=2" in misc_info
    assert "ab_update=true" in misc_info
    assert "avb_enable=true" in misc_info
    assert "avb_avbtool=avbtool" in misc_info
    assert "avb_boot_key_path=/tmp/testkey.pem" in misc_info


def test_calculate_min_partition_size_for_image() -> None:
    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)

    # Simulate max_image_size = partition_size - 8192.
    repacker._calc_avb_max_image_size = lambda _a, _b, p: p - 8192  # type: ignore[method-assign]

    # Need at least image_size + 8192, rounded to 4K.
    result = repacker._calculate_min_partition_size_for_image(
        Path("/tmp/avbtool"), "add_hashtree_footer", image_size=100000
    )
    assert result == 110592


def test_calculate_min_partition_size_retries_on_invalid_probe() -> None:
    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)

    # Simulate avbtool failure for small partition sizes.
    repacker._try_calc_avb_max_image_size = (  # type: ignore[method-assign]
        lambda _a, _b, p: None if p < 131072 else p - 8192
    )

    result = repacker._calculate_min_partition_size_for_image(
        Path("/tmp/avbtool"), "add_hashtree_footer", image_size=100000
    )
    assert result >= 110592


def test_apply_avb_to_custom_images_signs_non_aosp_partitions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "countrycode.img").write_bytes(b"x")
    (images_out / "mi_ext.img").write_bytes(b"x")
    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)
    repacker.shell = MagicMock()
    repacker.shell.run = MagicMock()

    monkeypatch.setattr(
        repacker,
        "_collect_stock_avb_profile",
        lambda: {
            "hash_parts": {"countrycode"},
            "hashtree_parts": {"mi_ext"},
        },
    )
    monkeypatch.setattr(
        repacker,
        "_calculate_min_partition_size_for_image",
        lambda _avbtool, _footer_cmd, image_size: image_size + 4096,
    )
    monkeypatch.setattr(
        repacker,
        "_build_footer_props_args",
        lambda part, include_hash_algorithm: [  # type: ignore[no-untyped-def]
            "--prop",
            f"com.android.build.{part}.fingerprint:test/fp",
        ],
    )

    repacker._apply_avb_to_custom_images(["countrycode", "mi_ext"])

    cmds = [call.args[0] for call in repacker.shell.run.call_args_list]
    assert any("add_hash_footer" in cmd for cmd in cmds)
    assert any("add_hashtree_footer" in cmd for cmd in cmds)
    assert any("com.android.build.countrycode.fingerprint:test/fp" in cmd for cmd in cmds)
    assert any("com.android.build.mi_ext.fingerprint:test/fp" in cmd for cmd in cmds)


def test_apply_avb_to_custom_images_chain_partitions_use_stock_size(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    stock_images = tmp_path / "build/stockrom/images"
    stock_images.mkdir(parents=True)

    # boot image has trailing zero padding and can be trimmed.
    (images_out / "boot.img").write_bytes(b"A" * 64 + b"\x00" * 64)
    (stock_images / "boot.img").write_bytes(b"S" * 96)
    (stock_images / "vbmeta.img").write_bytes(b"vbmeta")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)
    repacker.shell = MagicMock()
    repacker.shell.run = MagicMock()

    monkeypatch.setattr(
        repacker,
        "_collect_stock_avb_profile",
        lambda: {
            "hash_parts": set(),
            "hashtree_parts": set(),
            "chain_parts": [("boot", 3)],
        },
    )
    key_path = tmp_path / "otatools/security/testkey.pem"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("dummy", encoding="utf-8")
    monkeypatch.setattr(repacker, "_get_avb_testkey_path", lambda: key_path)
    monkeypatch.setattr(repacker, "_algorithm_for_key", lambda preferred, _key: preferred)
    monkeypatch.setattr(
        repacker,
        "_run_avbtool_info_image",
        lambda _avbtool, _image: {},
    )
    monkeypatch.setattr(
        repacker,
        "_calc_avb_max_image_size",
        lambda _avbtool, _footer_cmd, partition_size: partition_size - 16,
    )
    monkeypatch.setattr(
        repacker,
        "_build_footer_props_args",
        lambda _part, include_hash_algorithm=False: [],  # type: ignore[no-untyped-def]
    )

    repacker._apply_avb_to_custom_images(["boot"])

    cmd = repacker.shell.run.call_args.args[0]
    partition_size = cmd[cmd.index("--partition_size") + 1]
    assert partition_size == "96"
    assert (images_out / "boot.img").stat().st_size == 128


def test_apply_avb_to_custom_images_physical_hash_partitions_use_stock_size(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    stock_images = tmp_path / "build/stockrom/images"
    stock_images.mkdir(parents=True)

    (images_out / "vendor_boot.img").write_bytes(b"A" * 64 + b"\x00" * 64)
    (stock_images / "vendor_boot.img").write_bytes(b"S" * 96)
    (stock_images / "vbmeta.img").write_bytes(b"vbmeta")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)
    repacker.shell = MagicMock()
    repacker.shell.run = MagicMock()

    monkeypatch.setattr(
        repacker,
        "_collect_stock_avb_profile",
        lambda: {
            "hash_parts": {"vendor_boot"},
            "hashtree_parts": set(),
            "chain_parts": [],
        },
    )
    monkeypatch.setattr(
        repacker,
        "_calculate_min_partition_size_for_image",
        lambda _avbtool, _footer_cmd, _image_size: 128,
    )
    monkeypatch.setattr(
        repacker,
        "_calc_avb_max_image_size",
        lambda _avbtool, _footer_cmd, partition_size: partition_size - 16,
    )
    monkeypatch.setattr(
        repacker,
        "_build_footer_props_args",
        lambda _part, include_hash_algorithm=False: [],  # type: ignore[no-untyped-def]
    )

    repacker._apply_avb_to_custom_images(["vendor_boot"])

    cmd = repacker.shell.run.call_args.args[0]
    partition_size = cmd[cmd.index("--partition_size") + 1]
    assert partition_size == "96"
    assert (images_out / "vendor_boot.img").stat().st_size == 128


def test_verify_avb_images_runs_verify_image(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "vbmeta.img").write_bytes(b"x")
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)
    repacker.shell = MagicMock()
    repacker.shell.run = MagicMock()

    repacker._verify_avb_images()

    cmd = repacker.shell.run.call_args.args[0]
    assert "verify_image" in cmd
    assert "--follow_chain_partitions" in cmd


def test_verify_avb_images_skips_when_vbmeta_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)
    repacker.shell = MagicMock()
    repacker.shell.run = MagicMock()

    repacker._verify_avb_images()

    repacker.shell.run.assert_not_called()


def test_rebuild_vbmeta_images_follows_stock_structure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    meta_out = tmp_path / "out/target/product/pudding/META"
    images_out.mkdir(parents=True)
    meta_out.mkdir(parents=True)
    for name in (
        "boot.img",
        "recovery.img",
        "vbmeta_system.img",
        "system.img",
        "system_ext.img",
        "product.img",
        "dtbo.img",
    ):
        (images_out / name).write_bytes(b"x")

    key_path = tmp_path / "otatools/build/make/target/product/security/testkey.pem"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("dummy", encoding="utf-8")
    avbtool = tmp_path / "otatools/bin/avbtool"
    avbtool.parent.mkdir(parents=True)
    avbtool.write_text("", encoding="utf-8")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
    )
    repacker = Repacker(ctx)
    repacker.shell = MagicMock()
    repacker.shell.run = MagicMock()

    monkeypatch.setattr(
        repacker,
        "_collect_stock_avb_profile",
        lambda: {
            "vbmeta": {"algorithm": "SHA256_RSA4096", "rollback_index": 0, "flags": 0},
            "vbmeta_system": {
                "algorithm": "SHA256_RSA4096",
                "rollback_index": 1767225600,
                "flags": 0,
                "hashtree_partitions": ["system", "system_ext", "product"],
            },
            "hash_parts": {"dtbo"},
            "hashtree_parts": {"system", "system_ext", "product"},
            "chain_parts": [("boot", 3), ("recovery", 1), ("vbmeta_system", 2)],
        },
    )

    def fake_check_output(cmd, text, stderr, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(cmd, list) and "--output" in cmd:
            output_idx = cmd.index("--output")
            Path(cmd[output_idx + 1]).write_bytes(b"pub")
            return ""
        if isinstance(cmd, list) and cmd[:2] == ["openssl", "pkey"]:
            return "Private-Key: (2048 bit, 2 primes)\n"
        return ""

    monkeypatch.setattr("src.core.packer.subprocess.check_output", fake_check_output)

    repacker._rebuild_vbmeta_images(
        ["boot", "recovery", "vbmeta_system", "system", "system_ext", "product", "dtbo"]
    )

    all_cmds = [call.args[0] for call in repacker.shell.run.call_args_list]
    assert any("make_vbmeta_image" in cmd for cmd in all_cmds)
    top_cmd = all_cmds[-1]
    assert "--chain_partition" in top_cmd
    assert any("boot:3:" in str(part) for part in top_cmd)


def test_pack_ota_payload_skips_custom_avb_chain_when_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "system.img").write_bytes(b"x")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
        target_dir=target,
        repack_images_dir=tmp_path / "repack_images",
        enable_custom_avb_chain=False,
    )
    ctx.repack_images_dir.mkdir()
    repacker = Repacker(ctx)
    repacker._generate_meta_info = MagicMock()  # type: ignore[method-assign]
    repacker._copy_build_props = MagicMock()  # type: ignore[method-assign]
    repacker._run_ota_tool = MagicMock()  # type: ignore[method-assign]
    repacker._apply_avb_to_custom_images = MagicMock()  # type: ignore[method-assign]
    repacker._rebuild_vbmeta_images = MagicMock()  # type: ignore[method-assign]
    repacker._verify_avb_images = MagicMock()  # type: ignore[method-assign]

    repacker.pack_ota_payload()

    repacker._apply_avb_to_custom_images.assert_not_called()
    repacker._rebuild_vbmeta_images.assert_not_called()
    repacker._verify_avb_images.assert_not_called()


def test_pack_ota_payload_runs_custom_avb_chain_when_enabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "system.img").write_bytes(b"x")

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
        target_dir=target,
        repack_images_dir=tmp_path / "repack_images",
        enable_custom_avb_chain=True,
    )
    ctx.repack_images_dir.mkdir()
    repacker = Repacker(ctx)
    repacker._generate_meta_info = MagicMock()  # type: ignore[method-assign]
    repacker._copy_build_props = MagicMock()  # type: ignore[method-assign]
    repacker._run_ota_tool = MagicMock()  # type: ignore[method-assign]
    repacker._apply_avb_to_custom_images = MagicMock()  # type: ignore[method-assign]
    repacker._rebuild_vbmeta_images = MagicMock()  # type: ignore[method-assign]
    repacker._verify_avb_images = MagicMock()  # type: ignore[method-assign]

    repacker.pack_ota_payload()

    repacker._apply_avb_to_custom_images.assert_called_once()
    repacker._rebuild_vbmeta_images.assert_called_once()
    repacker._verify_avb_images.assert_called_once()


def test_build_footer_props_args_from_target_props(monkeypatch, tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    (target_dir / "system").mkdir(parents=True)
    (target_dir / "vendor").mkdir(parents=True)
    (target_dir / "system" / "build.prop").write_text(
        "ro.build.version.release=16\n"
        "ro.build.version.security_patch=2026-01-01\n"
        "ro.build.fingerprint=foo/system\n",
        encoding="utf-8",
    )
    (target_dir / "vendor" / "build.prop").write_text(
        "ro.vendor.build.fingerprint=foo/vendor\n",
        encoding="utf-8",
    )

    def get_target_prop_file(part: str):  # type: ignore[no-untyped-def]
        p = target_dir / part / "build.prop"
        return p if p.exists() else None

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
        target_dir=target_dir,
        get_target_prop_file=get_target_prop_file,
    )
    repacker = Repacker(ctx)
    args = repacker._build_footer_props_args("vendor", include_hash_algorithm=True)

    joined = " ".join(args)
    assert "--hash_algorithm sha256" in joined
    assert "com.android.build.vendor.fingerprint:foo/vendor" in joined
    assert "com.android.build.vendor.os_version:16" in joined
    assert "com.android.build.vendor.security_patch:2026-01-01" in joined


def test_virtual_ab_compression_method_added_when_enabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "system.img").write_bytes(b"x")

    target_dir = tmp_path / "target"
    (target_dir / "vendor").mkdir(parents=True)
    (target_dir / "vendor" / "build.prop").write_text(
        "ro.virtual_ab.compression.enabled=true\n",
        encoding="utf-8",
    )

    def get_target_prop_file(part: str):  # type: ignore[no-untyped-def]
        p = target_dir / part / "build.prop"
        return p if p.exists() else None

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
        target_dir=target_dir,
        get_target_prop_file=get_target_prop_file,
    )
    repacker = Repacker(ctx)

    monkeypatch.setattr(
        repacker,
        "_build_avb_misc_lines_from_stock",
        lambda _parts: ["avb_enable=true"],
    )

    repacker._generate_meta_info()

    dp_info = (tmp_path / "out/target/product/pudding/META/dynamic_partitions_info.txt").read_text(
        encoding="utf-8"
    )
    misc_info = (tmp_path / "out/target/product/pudding/META/misc_info.txt").read_text(
        encoding="utf-8"
    )
    for content in [dp_info, misc_info]:
        assert "virtual_ab_compression=true" in content
        assert "virtual_ab_compression_method=lz4" in content
        assert "virtual_ab_cow_version=3" in content
        assert "virtual_ab_compression_factor=65536" in content


def test_virtual_ab_compression_method_not_added_when_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "system.img").write_bytes(b"x")

    target_dir = tmp_path / "target"
    (target_dir / "vendor").mkdir(parents=True)
    (target_dir / "vendor" / "build.prop").write_text(
        "ro.virtual_ab.compression.enabled=false\n",
        encoding="utf-8",
    )

    def get_target_prop_file(part: str):  # type: ignore[no-untyped-def]
        p = target_dir / part / "build.prop"
        return p if p.exists() else None

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
        target_dir=target_dir,
        get_target_prop_file=get_target_prop_file,
    )
    repacker = Repacker(ctx)

    monkeypatch.setattr(
        repacker,
        "_build_avb_misc_lines_from_stock",
        lambda _parts: ["avb_enable=true"],
    )

    repacker._generate_meta_info()

    dp_info = (tmp_path / "out/target/product/pudding/META/dynamic_partitions_info.txt").read_text(
        encoding="utf-8"
    )
    misc_info = (tmp_path / "out/target/product/pudding/META/misc_info.txt").read_text(
        encoding="utf-8"
    )
    for content in [dp_info, misc_info]:
        assert "virtual_ab_compression=true" not in content
        assert "virtual_ab_compression_method=lz4" not in content
        assert "virtual_ab_cow_version=3" not in content
        assert "virtual_ab_compression_factor=65536" not in content
    assert "virtual_ab_compression_method=lz4" not in dp_info
    assert "virtual_ab_cow_version=3" not in dp_info
    assert "virtual_ab_compression_factor=65536" not in dp_info


def test_virtual_ab_uses_metadata_from_partition_info(monkeypatch, tmp_path: Path) -> None:
    """Test that VABC settings are read from partition_info.json when available."""
    monkeypatch.chdir(tmp_path)
    images_out = tmp_path / "out/target/product/pudding/IMAGES"
    images_out.mkdir(parents=True)
    (images_out / "system.img").write_bytes(b"x")

    target_dir = tmp_path / "target"
    (target_dir / "vendor").mkdir(parents=True)
    (target_dir / "vendor" / "build.prop").write_text(
        "ro.virtual_ab.compression.enabled=false\n",
        encoding="utf-8",
    )

    devices_dir = tmp_path / "devices" / "pudding"
    devices_dir.mkdir(parents=True)
    partition_info_path = devices_dir / "partition_info.json"
    partition_info_path.write_text(
        json.dumps(
            {
                "device_code": "pudding",
                "super_size": 13411287040,
                "dynamic_partitions": ["system", "vendor"],
                "dynamic_partition_metadata": {
                    "cow_version": 3,
                    "compression_factor": 32768,
                    "snapshot_enabled": True,
                    "vabc_enabled": True,
                    "vabc_compression_param": "gz",
                },
            }
        ),
        encoding="utf-8",
    )

    def get_target_prop_file(part: str):
        p = target_dir / part / "build.prop"
        return p if p.exists() else None

    ctx = SimpleNamespace(
        stock_rom_code="pudding",
        device_config={"pack": {"super_size": 13411287040}},
        target_dir=target_dir,
        get_target_prop_file=get_target_prop_file,
    )
    repacker = Repacker(ctx)

    monkeypatch.setattr(
        repacker,
        "_build_avb_misc_lines_from_stock",
        lambda _parts: ["avb_enable=true"],
    )

    repacker._generate_meta_info()

    dp_info = (tmp_path / "out/target/product/pudding/META/dynamic_partitions_info.txt").read_text(
        encoding="utf-8"
    )
    misc_info = (tmp_path / "out/target/product/pudding/META/misc_info.txt").read_text(
        encoding="utf-8"
    )
    for content in [dp_info, misc_info]:
        assert "virtual_ab_compression=true" in content
        assert "virtual_ab_compression_method=gz" in content
        assert "virtual_ab_cow_version=3" in content
        assert "virtual_ab_compression_factor=32768" in content
