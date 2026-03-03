import subprocess
import platform
import os
import logging
from pathlib import Path
from typing import List, Union, Optional, Callable

class ShellRunner:
    def __init__(self):
        self.logger = logging.getLogger("Shell")
        
        system = platform.system().lower()
        if system == "darwin":
            self.os_name = "darwin" # macOS
        elif system == "linux":
            self.os_name = "linux"
        else:
            self.os_name = "windows" 

        machine = platform.machine().lower()
        if machine in ["x86_64", "amd64"]:
            self.arch = "x86_64"
        elif machine in ["aarch64", "arm64"]:
            self.arch = "aarch64"
        else:
            self.arch = "x86_64" # 默认 fallback

        project_root = Path(__file__).resolve().parent.parent.parent
        self.bin_dir = project_root / "bin" / self.os_name / self.arch

        if not self.bin_dir.exists():
            self.logger.warning(f"Binary directory not found: {self.bin_dir}")
            
        self.otatools_bin = project_root / "otatools" / "bin"

    def get_binary_path(self, tool_name: str) -> Path:
        """
        Get the absolute path of the tool.
        Search Order:
        1. bin/{os}/{arch}/ (Platform specific tools)
        2. otatools/bin/ (Google OTA tools)
        3. bin/ (Common tools)
        4. System PATH
        """
        # 1. Platform specific
        bin_path = self.bin_dir / tool_name
        if bin_path.exists():
            return bin_path

        # 2. OTATools
        ota_path = self.otatools_bin / tool_name
        if ota_path.exists():
            return ota_path

        # 3. Common bin
        common_bin = self.bin_dir.parent.parent / tool_name
        if common_bin.exists():
            return common_bin

        # 4. Fallback to command name (relies on PATH)
        return Path(tool_name)

    def run(self, cmd: Union[str, List[str]], cwd: Optional[Path] = None, 
            check: bool = True, capture_output: bool = False, 
            env: Optional[dict] = None, logger: Optional[logging.Logger] = None,
            on_line: Optional[Callable[[str], None]] = None) -> subprocess.CompletedProcess:
        """
        Core method to execute commands
        :param cmd: List of commands (recommended) or string. e.g. ["lpunpack", "super.img"]
        :param cwd: Working directory for execution
        :param check: If True, raise exception when command returns non-zero
        :param capture_output: Whether to capture stdout/stderr (do not print directly to console)
        :param env: Environment variables dict (will merge with system env)
        :param logger: Optional logger to stream output to (forces capture_output=True)
        :param on_line: Optional callback function called for each line of output
        """
        
        if isinstance(cmd, list):
            tool = cmd[0]
            tool_path = self.get_binary_path(tool)
            if tool_path.is_absolute() and tool_path.exists():
                cmd[0] = str(tool_path)
                if not os.access(tool_path, os.X_OK):
                    os.chmod(tool_path, 0o755)
        
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
            
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        self.logger.debug(f"Running: {cmd_str}")

        # If a logger or on_line is provided, we must capture output
        should_capture = capture_output or (logger is not None) or (on_line is not None)

        try:
            if logger or on_line:
                # Streaming mode
                process = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    shell=(isinstance(cmd, str)),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=run_env
                )
                
                output_lines = []
                if process.stdout:
                    for line in process.stdout:
                        clean_line = line.strip()
                        if on_line:
                            on_line(clean_line)
                        
                        if logger and clean_line:
                            logger.info(f"  [SHELL] {clean_line}")
                        output_lines.append(line)
                
                returncode = process.wait()
                stdout = "".join(output_lines)
                
                if check and returncode != 0:
                    raise subprocess.CalledProcessError(returncode, cmd, output=stdout)
                
                return subprocess.CompletedProcess(cmd, returncode, stdout, "")
            else:
                # Normal mode
                result = subprocess.run(
                    cmd,
                    cwd=cwd,
                    check=check,
                    shell=(isinstance(cmd, str)),
                    text=True,
                    capture_output=should_capture,
                    env=run_env
                )
                return result
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed with return code {e.returncode}")
            self.logger.error(f"Command: {cmd_str}")
            if hasattr(e, 'stderr') and e.stderr:
                self.logger.error(f"Stderr: {e.stderr.strip()}")
            elif hasattr(e, 'output') and e.output:
                self.logger.error(f"Output: {e.output.strip()}")
            raise e

    def run_java_jar(self, jar_path: Union[str, Path], args: List[str], **kwargs):
        """Helper method specifically for executing java -jar commands"""
        full_jar_path = self.get_binary_path(str(jar_path))
        cmd = ["java", "-jar", str(full_jar_path)] + args
        return self.run(cmd, **kwargs)
