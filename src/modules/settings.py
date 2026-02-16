from pathlib import Path
from .base import BaseModule

class SettingsModule(BaseModule):
    def run(self, work_dir: Path):
        self.logger.info("Processing Settings.apk...")
        
        # Corresponds to Shell: if [[ ${is_port_eu_rom} == true ]]
        is_eu = getattr(self.ctx, "is_eu_port", False)

        if is_eu:
            self.logger.info("  -> Applying EU specific patches...")
            # setting_unlock_google_buttion (Unlock Google Button)
            self.smali_patch(work_dir, 
                iname="MiuiSettings.smali", 
                method="updateHeaderList", 
                regex_replace=(r"sget-boolean\s+(v\d+|p\d+),.*IS_GLOBAL_BUILD:Z", r"const/4 \1, 0x1")
            )
        else:
            # Only needed for non-EU versions (usually modified CN versions), as EU versions might have their own logic
            self.logger.info("  -> Applying CN specific patches...")
            
            # 1. Expand local register capacity (Ensure v3, v4 are available even if .locals is small)
            self.smali_patch(work_dir, 
                iname="IconDisplayCustomizationSettings.smali",
                method="setupShowNotificationIconCount", 
                # Find .locals X, replace with .locals 7 (give enough margin)
                regex_replace=(r"\.locals\s+\d+", r".locals 7")
            )

            # 2. Replace array instructions (Safely use borrowed registers \1\2\3 and newly allocated v5, v6)
            regex = r'filled-new-array\s*\{([vp]\d+),\s*([vp]\d+),\s*([vp]\d+)\},\s*\[I'
            repl = (
                r'const/4 \1, 0x0\n'
                r'    const/4 \2, 0x1\n'
                r'    const/4 \3, 0x3\n'
                r'    const/4 v5, 0x5\n'   # Use v5 (will not conflict with v0-v4)
                r'    const/4 v6, 0x7\n'   # Use v6
                r'    filled-new-array {\1, \2, \3, v5, v6}, [I'
            )
            
            self.smali_patch(work_dir, 
                iname="IconDisplayCustomizationSettings.smali",
                method="setupShowNotificationIconCount", 
                regex_replace=(regex, repl)
            )
            
            # 3. settings_resources_add_icons_5_and_7 (XML Injection)
            res_dir = self.xml.get_res_dir(work_dir)
            
            # A. Add Multi-language String
            self.logger.info("  -> Injecting 5/7 icons strings...")
            # Default Language (English)
            self.xml.add_string(res_dir, "display_notification_icon_5", "%d icons")
            self.xml.add_string(res_dir, "display_notification_icon_7", "%d icons")
            
            # Chinese (If original exists, keep placeholders consistent, or hardcode)
            # Suggest hardcoding Chinese, as original "None" has no placeholder. If original has placeholder, use "%d icons"
            self.xml.add_string(res_dir, "display_notification_icon_5", "显示%d个", "zh-rCN")
            self.xml.add_string(res_dir, "display_notification_icon_7", "显示%d个", "zh-rCN")
            
            # B. Add references to entries and values arrays
            self.logger.info("  -> Patching arrays...")
            
            # Fix entries array values, must match string names perfectly
            entries_to_add = [
                "@string/display_notification_icon_5",
                "@string/display_notification_icon_7"
            ]
            self.xml.add_array_item(res_dir, 
                array_name="notification_icon_counts_entries", 
                items=entries_to_add
            )
            
            # Fix values array values (Correct, 5 and 7)
            values_to_add = ["5", "7"]
            self.xml.add_array_item(res_dir,
                array_name="notification_icon_counts_values",
                items=values_to_add
            )
