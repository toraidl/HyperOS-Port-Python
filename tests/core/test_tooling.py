import logging

from src.core.tooling import resolve_tooling


def test_resolve_tooling_prefers_arch_specific_directory(tmp_path):
    platform_dir = tmp_path / "bin" / "linux" / "x86_64"
    platform_dir.mkdir(parents=True)
    (platform_dir / "magiskboot").touch()

    resolved = resolve_tooling(tmp_path, logging.getLogger("test"))

    assert resolved.platform_bin_dir == platform_dir
    assert resolved.tools.magiskboot == platform_dir / "magiskboot"


def test_resolve_tooling_falls_back_to_platform_directory(tmp_path):
    fallback_dir = tmp_path / "bin" / "linux"
    fallback_dir.mkdir(parents=True)

    resolved = resolve_tooling(tmp_path, logging.getLogger("test"))

    assert resolved.platform_bin_dir == fallback_dir
