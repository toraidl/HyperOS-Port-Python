import re
from pathlib import Path
from .base import BaseModule

class InstallerModule(BaseModule):
    def run(self, work_dir: Path):
        self.logger.info("Processing MIUIPackageInstaller.apk...")
        
        # 1. Disable install risk switches (various switches set to False)
        self._disable_install_risk_switches(work_dir)
        
        # 2. Disable Safe Mode (SafeModeTipView)
        self._disable_safemode(work_dir)
        
        # 3. Disable Risk Count Check (RiskControlRules)
        self._disable_risk_count_check(work_dir)
        
        # 4. Disable Full Safe Version (FullSafeVersion)
        self._disable_full_safe_version(work_dir)
        
        # 5. Disable App Info Upload and Intercept (AVL/Intercept)
        #self._disable_upload_and_intercept(work_dir)
        
        # 6. Remove Network Error UI (Network Error)
        #self._remove_network_error_ui(work_dir)

    def _disable_install_risk_switches(self, work_dir: Path):
        """Corresponds to miuipackageinstaller_install_risk_disable"""
        self.logger.info("Disabling risk install switches...")
        
        # Change return value of all following methods to False (0)
        targets = [
            "\"secure_verify_enable\"", 
            "\"installerOpenSafetyModel\"",
            "\"app_store_recommend\"",
            "\"ads_enable\"",
            "\"virus_scan_install\""
        ]
        
        for keyword in targets:
            self.smali_patch(work_dir, 
                seek_keyword=keyword, 
                return_type="Z", 
                remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
            )

    def _disable_safemode(self, work_dir: Path):
        """Corresponds to miuipackageinstaller_disable_safemode"""
        self.logger.info("Disabling Safe Mode...")

        # PART 1: Modify MiuiSettings$Ad
        # Find files containing string "android.provider.MiuiSettings$Ad"
        # Usually this class is responsible for checking whether to show ads/recommendations
        self.smali_patch(work_dir, 
            seek_keyword="android.provider.MiuiSettings$Ad", 
            return_type="Z", 
            remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
        )

        # PART 2: Modify SafeModeTipViewObject parent class field
        # 1. Find SafeModeTipViewObject.smali
        tip_file = None
        for f in work_dir.rglob("SafeModeTipViewObject.smali"):
            tip_file = f
            break
        
        if not tip_file:
            self.logger.warning("SafeModeTipViewObject.smali not found.")
            return

        # 2. Find parent class
        content = tip_file.read_text(encoding='utf-8')
        super_match = re.search(r"\.super L(.*?);", content)
        if not super_match: return
        
        super_class_path = super_match.group(1) # e.g. com/miui/packageinstaller/model/a
        super_class_name = super_class_path.split("/")[-1] # a
        
        # 3. Find parent class file
        super_file = None
        for f in work_dir.rglob(f"{super_class_name}.smali"):
            # Simple check if path suffix matches
            if str(f).endswith(f"{super_class_path}.smali"):
                super_file = f
                break
        
        if not super_file: return
        
        # 4. Find boolean field in parent class (.field ... :Z)
        super_content = super_file.read_text(encoding='utf-8')
        field_match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Z", super_content)
        if not field_match: return
        
        field_name = field_match.group(1)
        
        # 5. Hook method "a" of SafeModeTipViewObject
        # Inject logic: iput-boolean v0, p0, SuperClass->Field:Z
        hook_code = f"""
    const/4 v0, 0x0
    iput-boolean v0, p0, L{super_class_path};->{field_name}:Z
    """
        # Regex replace: Match return-void or return vX, and insert code before it
        # Note: Expand locals first
        self.smali_patch(work_dir, 
            file_path=str(tip_file),
            method="a", 
            regex_replace=(r"\.locals 0", ".locals 1")
        )
        self.smali_patch(work_dir, 
            file_path=str(tip_file),
            method="a", 
            regex_replace=(r"(return-void|return\s+[vp]\d+)", f"{hook_code}\n    \\1")
        )

    def _disable_risk_count_check(self, work_dir: Path):
        """Corresponds to miuipackageinstaller_disable_count_checking"""
        self.smali_patch(work_dir, 
            iname="RiskControlRules.smali", 
            method="getCurrentLevel", 
            return_type="I", 
            remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
        )

    def _disable_full_safe_version(self, work_dir: Path):
        """Corresponds to miuipackageinstaller_disable_full_safe_version"""
        self.logger.info("Disabling Full Safe Version...")

        # PART 1: Hook method installer_full_safe_version
        self.smali_patch(work_dir, 
            seek_keyword="installer_full_safe_version", 
            return_type="Z", 
            remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
        )

        # PART 2: Modify FullSafeHelper static field
        # 1. Find class containing string "FullSafeHelper" (usually FullSafeHelper.smali)
        helper_file = None
        for f in work_dir.rglob("*.smali"):
            if "FullSafeHelper" in f.read_text(encoding='utf-8', errors='ignore'):
                helper_file = f
                break
        
        if not helper_file: return
        
        # 2. Sniff static field (boolean or Boolean)
        content = helper_file.read_text(encoding='utf-8')
        class_desc = re.search(r"\.class.* (L.*?;)", content).group(1)
        
        # Prefer primitive Z
        field_match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Z", content)
        is_boxed = False
        
        if not field_match:
            # Find Boxed Boolean
            field_match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Ljava/lang/Boolean;", content)
            is_boxed = True
            
        if not field_match: return
        field_name = field_match.group(1)
        
        # 3. Construct <clinit> Patch
        if is_boxed:
            patch_code = f"""
    sget-object v0, Ljava/lang/Boolean;->FALSE:Ljava/lang/Boolean;
    sput-object v0, {class_desc}->{field_name}:Ljava/lang/Boolean;
    """
        else:
            patch_code = f"""
    const/4 v0, 0x0
    sput-boolean v0, {class_desc}->{field_name}:Z
    """
        
        # 4. Inject into <clinit>
        self.smali_patch(work_dir, 
            file_path=str(helper_file), 
            method="<clinit>", 
            regex_replace=(r"\.locals 0", ".locals 1")
        )
        self.smali_patch(work_dir, 
            file_path=str(helper_file), 
            method="<clinit>", 
            regex_replace=(r"return-void", f"{patch_code}\n    return-void")
        )

    def _disable_upload_and_intercept(self, work_dir: Path):
        """Corresponds to miuipackageinstaller_disable_upload"""
        # 1. AVL Upload
        self.smali_patch(work_dir, 
            seek_keyword="appSourcepackageName", 
            remake=".locals 0\n    return-void"
        )
        
        # 2. Intercept Check (apk_bit) -> return null
        self.smali_patch(work_dir, 
            seek_keyword="apk_bit", 
            remake=".locals 1\n    const/4 v0, 0x0\n    return-object v0"
        )
        
        # 3. Layout Info Request (Specific signature) -> return void
        # Signature: (String, String, String, Integer, String, String)
        sig = "Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/Integer;Ljava/lang/String;Ljava/lang/String;"
        self.smali_patch(work_dir, 
            seek_keyword=sig, 
            remake=".locals 0\n    return-void"
        )

    def _remove_network_error_ui(self, work_dir: Path):
        """Corresponds to miuipackageinstaller_remove_network_error_ui"""
        self.logger.info("Removing Network Error UI...")
        res_dir = self.xml.get_res_dir(work_dir)
        
        # 1. Find Layout ID
        layout_id = self.xml.get_id(res_dir, "layout_network_error")
        if layout_id:
            # Set all methods using this ID to empty (usually showNetworkError)
            self.smali_patch(work_dir, 
                seek_keyword=layout_id, 
                remake=".locals 0\n    return-void"
            )
        
        # 2. Find SafeMode Layout ID
        safe_id = self.xml.get_id(res_dir, "safe_mode_layout_network_error")
        if safe_id:
            self.smali_patch(work_dir, 
                seek_keyword=safe_id, 
                remake=".locals 0\n    return-void"
            )