import os
import shutil
import zipfile
import logging
import urllib.request
from pathlib import Path
from typing import Optional


class OtaToolsManager:
    # Hardcoded URL for otatools download
    DEFAULT_URL = "https://github.com/toraidl/HyperOS-Port-Python/releases/download/assets/otatools.zip"

    def __init__(self, tools_dir: Path = Path("otatools")):
        self.tools_dir = tools_dir
        self.logger = logging.getLogger("OtaToolsManager")

    def check_otatools_exists(self) -> bool:
        """Check if the otatools directory exists with all required components."""
        return self.tools_dir.exists() and any(self.tools_dir.iterdir())

    def download_otatools(self, url: Optional[str] = None) -> bool:
        """
        Download and extract otatools from given URL (or default URL).

        Args:
            url: URL to download otatools.zip from (optional, uses default if not provided)

        Returns:
            True if the download and extraction was successful, False otherwise
        """
        if url is None:
            url = self.DEFAULT_URL

        try:
            # Create temporary path for download
            temp_file = self.tools_dir.parent / "otatools_temp.zip"

            self.logger.info(f"Downloading otatools from {url}...")

            # Remove existing old temp file if exists
            if temp_file.exists():
                temp_file.unlink()

            # Actually download the tools
            urllib.request.urlretrieve(url, temp_file)

            self.logger.info("Download completed. Extracting...")

            # Create the directory if it doesn't exist
            self.tools_dir.mkdir(parents=True, exist_ok=True)

            # Extract the zip file
            with zipfile.ZipFile(temp_file, "r") as zip_ref:
                zip_ref.extractall(self.tools_dir)

            self.logger.info(f"otatools extracted to {self.tools_dir.resolve()}")

            # Set execute permissions on bin directory files
            bin_dir = self.tools_dir / "bin"
            if bin_dir.exists():
                for file in bin_dir.iterdir():
                    if file.is_file():
                        current_mode = file.stat().st_mode
                        file.chmod(
                            current_mode | 0o111
                        )  # Add execute permission for user, group, and others
                self.logger.info("Set execute permissions on otatools binaries")

            # Remove the temporary zip file
            temp_file.unlink()

            return True
        except Exception as e:
            self.logger.error(f"Failed to download and extract otatools: {str(e)}")
            return False

    def ensure_otatools(self) -> bool:
        """
        Check if otatools exists; if not, download it from the default URL.

        Returns:
            True if otatools is available or successfully downloaded, False otherwise
        """
        if self.check_otatools_exists():
            self.logger.info(f"otatools already exists at {self.tools_dir.resolve()}")
            return True

        self.logger.info(
            f"otatools directory does not exist. Attempting to download from {self.DEFAULT_URL}"
        )
        return self.download_otatools()
