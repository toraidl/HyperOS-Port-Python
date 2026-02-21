import re
import logging
from pathlib import Path
from .base import BaseModule

class DevicesOverlayModule(BaseModule):
    def run(self, work_dir: Path):
        self.logger.info("Processing DevicesAndroidOverlay.apk...")
        
        # Check Android Version
        # Only apply fix if Stock ROM Android version is < 16
        try:
            # Handle potential non-integer version strings gracefully
            version_str = getattr(self.ctx, "base_android_version", "0")
            # Extract major version if it's dot-separated (e.g. "14.0.0")
            if "." in version_str:
                version_str = version_str.split(".")[0]
            base_version = int(version_str)
        except (ValueError, TypeError):
            self.logger.warning(f"Could not parse Android version '{version_str}', defaulting to 0.")
            base_version = 0

        if base_version >= 16:
            self.logger.info(f"Stock Android version is {base_version} (>= 16). Skipping AOD fix.")
            return

        self.logger.info("  -> Fixing AOD and under-display fingerprint issues...")
        
        # Regex pattern from the shell script:
        # sed -i -E "s#(<string[[:space:]]+name="config_dozeComponent">)[^<]*#\1com.android.systemui/com.android.keyguard.doze.MiuiDozeService#g"
        
        # Python regex equivalent: \s matches [ \t\n\r\f\v], which covers [[:space:]]
        pattern = re.compile(r'(<string\s+name="config_dozeComponent">)[^<]*')
        replacement = r'\1com.android.systemui/com.android.keyguard.doze.MiuiDozeService'
        
        xml_files = list(work_dir.rglob("*.xml"))
        modified_count = 0
        
        for xml_file in xml_files:
            try:
                content = xml_file.read_text(encoding='utf-8', errors='ignore')
                
                # Check if the pattern exists before writing to avoid unnecessary IO
                if pattern.search(content):
                    new_content = pattern.sub(replacement, content)
                    
                    if new_content != content:
                        xml_file.write_text(new_content, encoding='utf-8')
                        self.logger.debug(f"Patched {xml_file.name}")
                        modified_count += 1
            except Exception as e:
                self.logger.error(f"Failed to process {xml_file}: {e}")
                
        self.logger.info(f"  -> Patch applied to {modified_count} XML files.")
