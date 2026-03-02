"""MIUI Package Installer modification plugin.

Disables various security checks and risk warnings in the installer.
"""
import re
from pathlib import Path

from src.core.modifiers.plugins.apk.base import ApkModifierPlugin, ApkModifierRegistry


@ApkModifierRegistry.register
class InstallerModifier(ApkModifierPlugin):
    """Modify MIUI Package Installer to disable security checks."""
    
    name = "installer_modifier"
    description = "Disable MIUI Package Installer security checks"
    apk_name = "MIUIPackageInstaller"
    priority = 60
    
    def _apply_patches(self, work_dir: Path):
        """Apply all installer patches."""
        self.logger.info("Processing MIUIPackageInstaller.apk...")
        
        # 1. Disable install risk switches
        self._disable_install_risk_switches(work_dir)
        
        # 2. Disable Safe Mode
        self._disable_safemode(work_dir)
        
        # 3. Disable Risk Count Check
        self._disable_risk_count_check(work_dir)
        
        # 4. Disable Full Safe Version
        self._disable_full_safe_version(work_dir)
        
        # 5. Disable App Info Upload and Intercept (commented out in original)
        # self._disable_upload_and_intercept(work_dir)
        
        # 6. Remove Network Error UI (commented out in original)
        # self._remove_network_error_ui(work_dir)
    
    def _disable_install_risk_switches(self, work_dir: Path):
        """Disable risk install switches by changing return values to False."""
        self.logger.info("Disabling risk install switches...")
        
        targets = [
            "\"secure_verify_enable\"", 
            "\"installerOpenSafetyModel\"",
            "\"app_store_recommend\"",
            "\"ads_enable\"",
            "\"virus_scan_install\""
        ]
        
        for keyword in targets:
            self.smali_seek_and_replace(
                work_dir, keyword,
                return_value="const/4 v0, 0x0",
                return_type="Z"
            )
    
    def _disable_safemode(self, work_dir: Path):
        """Disable Safe Mode."""
        self.logger.info("Disabling Safe Mode...")
        
        # PART 1: Modify MiuiSettings$Ad
        self.smali_seek_and_replace(
            work_dir,
            "android.provider.MiuiSettings$Ad",
            return_value="const/4 v0, 0x0",
            return_type="Z"
        )
        
        # PART 2: Modify SafeModeTipViewObject
        tip_file = self._find_file(work_dir, "SafeModeTipViewObject.smali")
        if not tip_file:
            self.logger.warning("SafeModeTipViewObject.smali not found.")
            return
        
        # Find parent class
        content = tip_file.read_text(encoding='utf-8')
        super_match = re.search(r"\.super L(.*?);", content)
        if not super_match:
            return
        
        super_class_path = super_match.group(1)
        super_class_name = super_class_path.split("/")[-1]
        
        # Find parent class file
        super_file = None
        for f in work_dir.rglob(f"{super_class_name}.smali"):
            if str(f).endswith(f"{super_class_path}.smali"):
                super_file = f
                break
        
        if not super_file:
            return
        
        # Find boolean field
        super_content = super_file.read_text(encoding='utf-8')
        field_match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Z", super_content)
        if not field_match:
            return
        
        field_name = field_match.group(1)
        
        # Hook method "a" to disable Safe Mode
        hook_code = f"""
    const/4 v0, 0x0
    iput-boolean v0, p0, L{super_class_path};->{field_name}:Z
    """
        
        self.smali_patch(
            work_dir,
            file_path=str(tip_file),
            method="a",
            regex_replace=(r"\.locals 0", ".locals 1")
        )
        self.smali_patch(
            work_dir,
            file_path=str(tip_file),
            method="a",
            regex_replace=(r"(return-void|return\s+[vp]\d+)", f"{hook_code}\n    \\1")
        )
    
    def _disable_risk_count_check(self, work_dir: Path):
        """Disable risk count checking."""
        self.smali_patch(
            work_dir,
            iname="RiskControlRules.smali",
            method="getCurrentLevel",
            return_type="I",
            remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
        )
    
    def _disable_full_safe_version(self, work_dir: Path):
        """Disable Full Safe Version."""
        self.logger.info("Disabling Full Safe Version...")
        
        # PART 1: Hook installer_full_safe_version
        self.smali_seek_and_replace(
            work_dir,
            "installer_full_safe_version",
            return_value="const/4 v0, 0x0",
            return_type="Z"
        )
        
        # PART 2: Modify FullSafeHelper static field
        helper_file = self._find_file_with_content(work_dir, "FullSafeHelper")
        if not helper_file:
            return
        
        content = helper_file.read_text(encoding='utf-8')
        class_match = re.search(r"\.class.* (L.*?;)", content)
        if not class_match:
            return
        
        class_desc = class_match.group(1)
        
        # Find boolean field (prefer primitive Z)
        field_match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Z", content)
        is_boxed = False
        
        if not field_match:
            field_match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Ljava/lang/Boolean;", content)
            is_boxed = True
        
        if not field_match:
            return
        
        field_name = field_match.group(1)
        
        # Construct <clinit> patch
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
        
        self.smali_patch(
            work_dir,
            file_path=str(helper_file),
            method="<clinit>",
            regex_replace=(r"\.locals 0", ".locals 1")
        )
        self.smali_patch(
            work_dir,
            file_path=str(helper_file),
            method="<clinit>",
            regex_replace=(r"return-void", f"{patch_code}\n    return-void")
        )
    
    def _disable_upload_and_intercept(self, work_dir: Path):
        """Disable AVL upload and intercept checks."""
        # AVL Upload
        self.smali_patch(
            work_dir,
            seek_keyword="appSourcepackageName",
            remake=".locals 0\n    return-void"
        )
        
        # Intercept Check
        self.smali_patch(
            work_dir,
            seek_keyword="apk_bit",
            remake=".locals 1\n    const/4 v0, 0x0\n    return-object v0"
        )
        
        # Layout Info Request
        sig = "Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/Integer;Ljava/lang/String;Ljava/lang/String;"
        self.smali_patch(
            work_dir,
            seek_keyword=sig,
            remake=".locals 0\n    return-void"
        )
    
    def _remove_network_error_ui(self, work_dir: Path):
        """Remove Network Error UI."""
        self.logger.info("Removing Network Error UI...")
        
        res_dir = work_dir / "res"
        
        # Find Layout IDs and disable their usage
        layout_ids = [
            "layout_network_error",
            "safe_mode_layout_network_error"
        ]
        
        for layout_id in layout_ids:
            # Note: In a real implementation, we'd need to parse public.xml
            # to get the actual ID number, then patch methods using that ID
            self.logger.debug(f"Would disable layout: {layout_id}")
    
    def _find_file(self, work_dir: Path, filename: str) -> Path | None:
        """Find a file in work directory."""
        for f in work_dir.rglob(filename):
            return f
        return None
    
    def _find_file_with_content(self, work_dir: Path, content: str) -> Path | None:
        """Find a file containing specific content."""
        for f in work_dir.rglob("*.smali"):
            try:
                if content in f.read_text(encoding='utf-8', errors='ignore'):
                    return f
            except:
                pass
        return None
