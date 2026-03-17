from argparse import Namespace
from unittest.mock import MagicMock, patch

from src.app.bootstrap import initialize_cache_manager
from src.app.workflow import DEFAULT_PHASES, execute_porting, run_modification_phases


def make_args(**overrides):
    base = {
        "stock": "stock.zip",
        "port": "port.zip",
        "ksu": False,
        "work_dir": "build",
        "clean": False,
        "debug": False,
        "pack_type": None,
        "fs_type": None,
        "eu_bundle": None,
        "phases": None,
        "cache_dir": ".cache/portroms",
        "no_cache": False,
        "enable_partition_cache": False,
        "clear_cache": False,
        "show_cache_stats": False,
        "preflight_only": False,
        "skip_preflight": False,
        "preflight_strict": False,
        "preflight_report": "build/preflight-report.json",
        "enable_snapshots": False,
        "snapshot_dir": None,
        "rollback_to_snapshot": None,
        "enable_diff_report": False,
        "diff_report": "build/diff-report.json",
    }
    base.update(overrides)
    return Namespace(**base)


def test_initialize_cache_manager_skips_official_mode():
    result = initialize_cache_manager(make_args(), is_official_modify=True, logger=MagicMock())

    assert result.cache_manager is None
    assert result.exit_code is None


def test_run_modification_phases_invokes_requested_modifiers():
    ctx = MagicMock()
    logger = MagicMock()

    with (
        patch("src.app.workflow.UnifiedModifier") as unified_modifier_cls,
        patch("src.app.workflow.FrameworkModifier") as framework_modifier_cls,
        patch("src.app.workflow.FirmwareModifier") as firmware_modifier_cls,
        patch("src.app.workflow.RomModifier") as rom_modifier_cls,
    ):
        unified_modifier = unified_modifier_cls.return_value
        unified_modifier.run.return_value = True

        run_modification_phases(ctx, ["system", "apk", "firmware"], logger)

    unified_modifier_cls.assert_called_once_with(ctx, enable_apk_mods=True)
    unified_modifier.run.assert_called_once_with(phases=["system", "apk"])
    framework_modifier_cls.assert_not_called()
    firmware_modifier_cls.assert_called_once_with(ctx)
    firmware_modifier_cls.return_value.run.assert_called_once()
    rom_modifier_cls.assert_called_once_with(ctx)
    rom_modifier_cls.return_value.run_all_modifications.assert_called_once()


def test_execute_porting_returns_zero_for_show_cache_stats():
    logger = MagicMock()
    args = make_args(show_cache_stats=True)

    with patch("src.app.workflow.initialize_cache_manager") as bootstrap:
        bootstrap.return_value.exit_code = 0
        bootstrap.return_value.cache_manager = None

        assert execute_porting(args, logger) == 0

    bootstrap.assert_called_once()


def test_execute_porting_returns_two_when_preflight_has_blockers():
    logger = MagicMock()
    args = make_args()

    with (
        patch("src.app.workflow.initialize_cache_manager") as bootstrap,
        patch("src.app.workflow.log_run_configuration"),
        patch("src.app.workflow.OtaToolsManager") as otatools_manager_cls,
        patch("src.app.workflow.resolve_remote_inputs"),
        patch("src.app.workflow.run_preflight") as run_preflight_mock,
        patch("src.app.workflow.save_preflight_report"),
    ):
        bootstrap.return_value.exit_code = None
        bootstrap.return_value.cache_manager = None
        otatools_manager_cls.return_value.ensure_otatools.return_value = True
        run_preflight_mock.return_value.has_failures.return_value = True

        assert execute_porting(args, logger) == 2


def test_execute_porting_preflight_only_exits_zero_after_success():
    logger = MagicMock()
    args = make_args(preflight_only=True)

    with (
        patch("src.app.workflow.initialize_cache_manager") as bootstrap,
        patch("src.app.workflow.log_run_configuration"),
        patch("src.app.workflow.OtaToolsManager") as otatools_manager_cls,
        patch("src.app.workflow.resolve_remote_inputs"),
        patch("src.app.workflow.run_preflight") as run_preflight_mock,
        patch("src.app.workflow.save_preflight_report"),
    ):
        bootstrap.return_value.exit_code = None
        bootstrap.return_value.cache_manager = None
        otatools_manager_cls.return_value.ensure_otatools.return_value = True
        run_preflight_mock.return_value.has_failures.return_value = False

        assert execute_porting(args, logger) == 0


def test_execute_porting_uses_default_phase_list():
    logger = MagicMock()
    args = make_args()

    with (
        patch("src.app.workflow.initialize_cache_manager") as bootstrap,
        patch("src.app.workflow.log_run_configuration"),
        patch("src.app.workflow.OtaToolsManager") as otatools_manager_cls,
        patch("src.app.workflow.resolve_remote_inputs"),
        patch("src.app.workflow.run_preflight") as run_preflight_mock,
        patch("src.app.workflow.save_preflight_report"),
        patch("src.app.workflow.resolve_work_paths") as resolve_work_paths,
        patch("src.app.workflow.RomPackage") as rom_package_cls,
        patch("src.app.workflow.PortingContext") as porting_context_cls,
        patch("src.app.workflow.load_device_config", return_value={}),
        patch("src.app.workflow.determine_pack_settings", return_value=("payload", "erofs")),
        patch("src.app.workflow.run_modification_phases") as run_modification_phases_mock,
        patch("src.app.workflow.run_repacking"),
    ):
        bootstrap.return_value.exit_code = None
        bootstrap.return_value.cache_manager = None
        otatools_manager_cls.return_value.ensure_otatools.return_value = True
        run_preflight_mock.return_value.has_failures.return_value = False
        resolve_work_paths.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        stock = rom_package_cls.return_value
        porting_context = porting_context_cls.return_value
        porting_context.stock = stock
        porting_context.device_config = {}

        assert execute_porting(args, logger) == 0

    run_modification_phases_mock.assert_called_once()
    assert run_modification_phases_mock.call_args.args[1] == DEFAULT_PHASES


def test_execute_porting_strict_preflight_treats_risks_as_failures():
    logger = MagicMock()
    args = make_args(preflight_strict=True)

    with (
        patch("src.app.workflow.initialize_cache_manager") as bootstrap,
        patch("src.app.workflow.log_run_configuration"),
        patch("src.app.workflow.OtaToolsManager") as otatools_manager_cls,
        patch("src.app.workflow.resolve_remote_inputs"),
        patch("src.app.workflow.run_preflight") as run_preflight_mock,
        patch("src.app.workflow.save_preflight_report"),
    ):
        bootstrap.return_value.exit_code = None
        bootstrap.return_value.cache_manager = None
        otatools_manager_cls.return_value.ensure_otatools.return_value = True
        run_preflight_mock.return_value.has_failures.return_value = True

        assert execute_porting(args, logger) == 2
        run_preflight_mock.return_value.has_failures.assert_called_once_with(strict=True)


def test_execute_porting_restores_snapshot_and_exits():
    logger = MagicMock()
    args = make_args(rollback_to_snapshot="phase3_modified")

    with (
        patch("src.app.workflow.initialize_cache_manager") as bootstrap,
        patch("src.app.workflow.log_run_configuration"),
        patch("src.app.workflow.OtaToolsManager") as otatools_manager_cls,
        patch("src.app.workflow.resolve_remote_inputs"),
        patch("src.app.workflow.StageSnapshotManager") as snapshot_manager_cls,
        patch("src.app.workflow.resolve_work_paths") as resolve_work_paths,
    ):
        bootstrap.return_value.exit_code = None
        bootstrap.return_value.cache_manager = None
        otatools_manager_cls.return_value.ensure_otatools.return_value = True
        resolve_work_paths.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )

        assert execute_porting(args, logger) == 0

    snapshot_manager_cls.return_value.restore.assert_called_once()


def test_execute_porting_captures_snapshots_when_enabled():
    logger = MagicMock()
    args = make_args(enable_snapshots=True)

    with (
        patch("src.app.workflow.initialize_cache_manager") as bootstrap,
        patch("src.app.workflow.log_run_configuration"),
        patch("src.app.workflow.OtaToolsManager") as otatools_manager_cls,
        patch("src.app.workflow.resolve_remote_inputs"),
        patch("src.app.workflow.run_preflight") as run_preflight_mock,
        patch("src.app.workflow.save_preflight_report"),
        patch("src.app.workflow.StageSnapshotManager") as snapshot_manager_cls,
        patch("src.app.workflow.resolve_work_paths") as resolve_work_paths,
        patch("src.app.workflow.RomPackage") as rom_package_cls,
        patch("src.app.workflow.PortingContext") as porting_context_cls,
        patch("src.app.workflow.load_device_config", return_value={}),
        patch("src.app.workflow.determine_pack_settings", return_value=("payload", "erofs")),
        patch("src.app.workflow.run_modification_phases"),
        patch("src.app.workflow.run_repacking"),
    ):
        bootstrap.return_value.exit_code = None
        bootstrap.return_value.cache_manager = None
        otatools_manager_cls.return_value.ensure_otatools.return_value = True
        run_preflight_mock.return_value.has_failures.return_value = False
        resolve_work_paths.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        stock = rom_package_cls.return_value
        porting_context = porting_context_cls.return_value
        porting_context.stock = stock
        porting_context.device_config = {}

        assert execute_porting(args, logger) == 0

    capture_calls = snapshot_manager_cls.return_value.capture.call_args_list
    assert [call.args[0] for call in capture_calls] == [
        "phase2_initialized",
        "phase3_modified",
        "phase4_repacked",
    ]


def test_execute_porting_generates_diff_report_when_enabled():
    logger = MagicMock()
    args = make_args(enable_diff_report=True)

    with (
        patch("src.app.workflow.initialize_cache_manager") as bootstrap,
        patch("src.app.workflow.log_run_configuration"),
        patch("src.app.workflow.OtaToolsManager") as otatools_manager_cls,
        patch("src.app.workflow.resolve_remote_inputs"),
        patch("src.app.workflow.run_preflight") as run_preflight_mock,
        patch("src.app.workflow.save_preflight_report"),
        patch("src.app.workflow.resolve_work_paths") as resolve_work_paths,
        patch("src.app.workflow.RomPackage") as rom_package_cls,
        patch("src.app.workflow.PortingContext") as porting_context_cls,
        patch("src.app.workflow.load_device_config", return_value={}),
        patch("src.app.workflow.determine_pack_settings", return_value=("payload", "erofs")),
        patch("src.app.workflow.run_modification_phases"),
        patch("src.app.workflow.run_repacking"),
        patch("src.app.workflow.collect_artifact_state") as collect_artifact_state_mock,
        patch(
            "src.app.workflow.generate_diff_report",
            return_value={
                "summary": {
                    "files_added": 1,
                    "files_removed": 0,
                    "files_modified": 2,
                    "prop_changes": 3,
                    "apk_changes": 4,
                    "risk_flags": 1,
                },
                "highlights": {
                    "risk_flags": [{"code": "HIGH_IMPACT_PATH_CHANGED"}],
                },
            },
        ) as generate_mock,
        patch("src.app.workflow.save_diff_report") as save_diff_report_mock,
    ):
        bootstrap.return_value.exit_code = None
        bootstrap.return_value.cache_manager = None
        otatools_manager_cls.return_value.ensure_otatools.return_value = True
        run_preflight_mock.return_value.has_failures.return_value = False
        resolve_work_paths.return_value = (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        stock = rom_package_cls.return_value
        porting_context = porting_context_cls.return_value
        porting_context.stock = stock
        porting_context.device_config = {}
        collect_artifact_state_mock.side_effect = [{"files": {}}, {"files": {"a": {}}}]

        assert execute_porting(args, logger) == 0

    assert collect_artifact_state_mock.call_count == 2
    generate_mock.assert_called_once()
    save_diff_report_mock.assert_called_once()
    logger.info.assert_any_call(
        "Artifact diff summary: +%s -%s ~%s props=%s apks=%s risks=%s",
        1,
        0,
        2,
        3,
        4,
        1,
    )
    logger.warning.assert_any_call(
        "Artifact diff risk flags: %s", "HIGH_IMPACT_PATH_CHANGED"
    )
