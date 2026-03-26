import pytest

from src.app.cli import parse_args


def test_parse_args_rejects_missing_local_port_path(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")

    with pytest.raises(SystemExit):
        parse_args(["--stock", str(stock), "--port", str(tmp_path / "missing-port.zip")])


def test_parse_args_allows_remote_port_path():
    args = parse_args(["--stock", "https://example.com/stock.zip", "--port", "https://example.com/port.zip"])

    assert args.stock == "https://example.com/stock.zip"
    assert args.port == "https://example.com/port.zip"


def test_parse_args_expands_comma_separated_phases(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")
    args = parse_args(["--stock", str(stock), "--phases", "system,apk", "framework"])

    assert args.phases == ["system", "apk", "framework"]


def test_parse_args_rejects_invalid_phase(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")
    with pytest.raises(SystemExit):
        parse_args(["--stock", str(stock), "--phases", "system,invalid"])


def test_parse_args_accepts_preflight_flags(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")
    args = parse_args(
        [
            "--stock",
            str(stock),
            "--preflight-only",
            "--preflight-report",
            "out/preflight.json",
        ]
    )

    assert args.preflight_only is True
    assert args.skip_preflight is False
    assert args.preflight_strict is False
    assert args.preflight_report == "out/preflight.json"


def test_parse_args_accepts_snapshot_flags(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")
    args = parse_args(
        [
            "--stock",
            str(stock),
            "--enable-snapshots",
            "--snapshot-dir",
            "build/snapshots",
            "--rollback-to-snapshot",
            "phase3_modified",
        ]
    )

    assert args.enable_snapshots is True
    assert args.snapshot_dir == "build/snapshots"
    assert args.rollback_to_snapshot == "phase3_modified"


def test_parse_args_accepts_diff_report_flags(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")
    args = parse_args(
        [
            "--stock",
            str(stock),
            "--enable-diff-report",
            "--diff-report",
            "out/diff-report.json",
        ]
    )

    assert args.enable_diff_report is True
    assert args.diff_report == "out/diff-report.json"


def test_parse_args_accepts_custom_avb_chain_flag(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")
    args = parse_args(["--stock", str(stock), "--custom-avb-chain"])

    assert args.custom_avb_chain is True


def test_parse_args_accepts_resume_from_packer_flag(tmp_path):
    stock = tmp_path / "stock.zip"
    stock.write_bytes(b"stub")
    args = parse_args(["--stock", str(stock), "--resume-from-packer"])

    assert args.resume_from_packer is True
