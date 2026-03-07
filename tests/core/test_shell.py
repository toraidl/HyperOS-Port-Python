"""Unit tests for ShellRunner class."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.utils.shell import ShellRunner


class TestShellRunner:
    """Test cases for ShellRunner class."""

    @pytest.fixture
    def runner(self):
        """Create a ShellRunner instance."""
        return ShellRunner()

    def test_init_sets_platform(self, runner):
        """Test that initialization sets platform attributes."""
        assert runner.os_name in ["linux", "darwin", "windows"]
        assert runner.arch in ["x86_64", "aarch64"]

    def test_get_binary_path_platform_specific(self, runner, tmp_path, monkeypatch):
        """Test finding binary in platform-specific directory."""
        # Setup mock binary directory
        runner.bin_dir = tmp_path / "bin"
        tool_path = runner.bin_dir / "testtool"
        runner.bin_dir.mkdir()
        tool_path.touch()

        result = runner.get_binary_path("testtool")
        assert result == tool_path

    def test_get_binary_path_otatools(self, runner, tmp_path, monkeypatch):
        """Test finding binary in otatools directory."""
        runner.otatools_bin = tmp_path / "otatools" / "bin"
        runner.otatools_bin.mkdir(parents=True)
        tool_path = runner.otatools_bin / "otatool"
        tool_path.touch()

        result = runner.get_binary_path("otatool")
        assert result == tool_path

    def test_get_binary_path_fallback(self, runner):
        """Test fallback to command name when binary not found."""
        result = runner.get_binary_path("nonexistent_tool_12345")
        assert result == Path("nonexistent_tool_12345")

    @patch("subprocess.run")
    def test_run_with_list_command(self, mock_run, runner):
        """Test running a command as a list."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "test"], returncode=0, stdout="test\n", stderr=""
        )

        result = runner.run(["echo", "test"])

        mock_run.assert_called_once()
        assert result.returncode == 0

    @patch("subprocess.run")
    def test_run_with_string_command(self, mock_run, runner):
        """Test running a command as a string."""
        mock_run.return_value = subprocess.CompletedProcess(
            args="echo test", returncode=0, stdout="test\n", stderr=""
        )

        result = runner.run("echo test", shell=True)

        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_run_raises_on_failure(self, mock_run, runner):
        """Test that run raises exception on command failure."""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["false"], output="", stderr="error message"
        )

        with pytest.raises(subprocess.CalledProcessError):
            runner.run(["false"])

    @patch("subprocess.run")
    def test_run_check_false_no_raise(self, mock_run, runner):
        """Test that check=False doesn't raise on failure."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["false"], returncode=1, stdout="", stderr=""
        )

        result = runner.run(["false"], check=False)
        assert result.returncode == 1

    @patch("subprocess.run")
    def test_run_with_cwd(self, mock_run, runner, tmp_path):
        """Test running command with specific working directory."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["pwd"], returncode=0, stdout=str(tmp_path) + "\n", stderr=""
        )

        runner.run(["pwd"], cwd=tmp_path)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == tmp_path

    @patch("subprocess.run")
    def test_run_with_env(self, mock_run, runner):
        """Test running command with custom environment variables."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["env"], returncode=0, stdout="", stderr=""
        )

        runner.run(["env"], env={"TEST_VAR": "test_value"})

        call_kwargs = mock_run.call_args[1]
        assert "TEST_VAR" in call_kwargs["env"]
        assert call_kwargs["env"]["TEST_VAR"] == "test_value"

    @patch("subprocess.Popen")
    def test_run_with_logger(self, mock_popen, runner, caplog):
        """Test running command with logger streams output."""
        mock_process = Mock()
        mock_process.stdout = iter(["line1\n", "line2\n"])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        logger = Mock()

        runner.run(["test"], logger=logger)

        mock_popen.assert_called_once()
        assert logger.info.called

    @patch("subprocess.Popen")
    def test_run_with_callback(self, mock_popen, runner):
        """Test running command with output callback."""
        mock_process = Mock()
        mock_process.stdout = iter(["line1\n", "line2\n"])
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        callback_lines = []
        runner.run(["test"], on_line=callback_lines.append)

        assert len(callback_lines) == 2
        assert "line1" in callback_lines
        assert "line2" in callback_lines

    def test_run_java_jar(self, runner):
        """Test helper for running Java JAR files."""
        with patch.object(runner, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["java", "-jar", "test.jar", "arg1"], returncode=0, stdout="", stderr=""
            )

            result = runner.run_java_jar("test.jar", ["arg1"])

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "java"
            assert call_args[1] == "-jar"
            assert "arg1" in call_args
