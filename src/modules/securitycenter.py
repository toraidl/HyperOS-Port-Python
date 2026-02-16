import re
from pathlib import Path
from .base import BaseModule

class SecurityCenterModule(BaseModule):
    def run(self, work_dir: Path):
        self.logger.info("Processing SecurityCenter.apk...")
        
        # 1. Battery Health (SOH) Patch and Display
        self._patch_battery_health(work_dir)
        
        # 2. Battery Temperature Patch and Display
        self._patch_temperature(work_dir)
        
        # 3. Remove Battery Capacity Lock
        self._remove_battery_lock(work_dir)
        
        # 4. Add Detailed Battery Info (Design/Current Capacity, Cycle Count)
        self._add_capacity_info(work_dir)
        
        # 5. Remove Intercept Timer on Permission Page
        self._remove_intercept_timer(work_dir)

    def _patch_battery_health(self, work_dir: Path):
        """Corresponds to securitycenter_patch_soh: Auto-detect field and inject SOH read/write logic"""
        self.logger.info("Applying Battery Health Patch...")
        
        # Step 0: Find ChargeProtectFragment$d.smali containing handleMessage
        # Usually ChargeProtectFragment$d.smali or similar inner class
        target_file = None
        for f in work_dir.rglob("ChargeProtectFragment$*.smali"):
            content = f.read_text(encoding='utf-8', errors='ignore')
            if "handleMessage" in content and "ChargeProtectFragment" in content:
                target_file = f
                break
        
        if not target_file:
            self.logger.warning("ChargeProtectFragment handler not found, skipping SOH patch.")
            return

        # Auto-detect WeakReference field name (e.g., .field private a:Ljava/lang/ref/WeakReference;)
        content = target_file.read_text(encoding='utf-8', errors='ignore')
        # Match last section of field name
        match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Ljava/lang/ref/WeakReference;", content)
        if not match:
            self.logger.warning("WeakReference field not detected, skipping SOH patch.")
            return
        
        weak_ref_field = match.group(1)
        self.logger.info(f"Detected WeakReference field: {weak_ref_field}")

        # Step 1: Writer (Intercept battery_health_soh write property)
        writer_code = """
    const-string v0, "sys.hack.soh"
    invoke-static {p0}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
    move-result-object v1
    invoke-static {v0, v1}, Landroid/os/SystemProperties;->set(Ljava/lang/String;Ljava/lang/String;)V"""
        
        # Note: Are we searching in SecurityCenter root or specific file?
        # Original script uses -seek "battery_health_soh", scope is whole directory
        self.smali_patch(work_dir, 
            seek_keyword="battery_health_soh", 
            regex_replace=(r"return-void", f"{writer_code}\n    return-void")
        )

        # Step 2: Reader (Read property in Handler and update UI)
        reader_code = f"""
    # [Patch Start] Update UI
    iget-object v0, p0, Lcom/miui/powercenter/nightcharge/ChargeProtectFragment$d;->{weak_ref_field}:Ljava/lang/ref/WeakReference;
    invoke-virtual {{v0}}, Ljava/lang/ref/Reference;->get()Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Lcom/miui/powercenter/nightcharge/ChargeProtectFragment;
    
    if-eqz v0, :soc_patch_end
    const-string v1, "sys.hack.soh"
    const-string v2, "--"
    invoke-static {{v1, v2}}, Landroid/os/SystemProperties;->get(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    
    new-instance v2, Ljava/lang/StringBuilder;
    invoke-direct {{v2}}, Ljava/lang/StringBuilder;-><init>()V
    invoke-virtual {{v2, v1}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    const-string v1, " %"
    invoke-virtual {{v2, v1}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {{v2}}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v1

    const-string v2, "reference_battery_health"
    invoke-virtual {{v0, v2}}, Landroidx/preference/PreferenceFragmentCompat;->findPreference(Ljava/lang/CharSequence;)Landroidx/preference/Preference;
    move-result-object v2
    if-eqz v2, :soc_patch_end
    check-cast v2, Lmiuix/preference/TextPreference;
    invoke-virtual {{v2, v1}}, Lmiuix/preference/TextPreference;->setText(Ljava/lang/String;)V
    :soc_patch_end
    """
        self.smali_patch(work_dir, 
            file_path=str(target_file), # Specify file precisely
            method="handleMessage", 
            regex_replace=(r"return-void", f"{reader_code}\n    return-void")
        )

    def _patch_temperature(self, work_dir: Path):
        """Corresponds to securitycenter_inject_temperature: Inject temperature display"""
        self.logger.info("Applying Temperature Patch...")
        
        # Reuse target_file and weak_ref_field finding logic
        target_file = None
        for f in work_dir.rglob("ChargeProtectFragment$*.smali"):
            if "handleMessage" in f.read_text(encoding='utf-8', errors='ignore'):
                target_file = f
                break
        
        if not target_file: return
        
        content = target_file.read_text(encoding='utf-8', errors='ignore')
        match = re.search(r"\.field.* ([a-zA-Z0-9_]+):Ljava/lang/ref/WeakReference;", content)
        if not match: return
        weak_ref_field = match.group(1)

        # Step 1: Writer (Intercept getBatteryTemperature return value)
        writer_code = """
    const-string v0, "sys.hack.temp"
    invoke-static {p0}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
    move-result-object v1
    invoke-static {v0, v1}, Landroid/os/SystemProperties;->set(Ljava/lang/String;Ljava/lang/String;)V"""
        
        self.smali_patch(work_dir,
            seek_keyword="getBatteryTemperature ",
            regex_replace=(r"return p0", f"{writer_code}\n    return p0")
        )

        # Step 2: Reader (UI Update)
        # Unicode \\u00b0C needs escaping in Python string
        reader_code = f"""
    # [Patch Start] Update Temp
    iget-object v0, p0, Lcom/miui/powercenter/nightcharge/ChargeProtectFragment$d;->{weak_ref_field}:Ljava/lang/ref/WeakReference;
    invoke-virtual {{v0}}, Ljava/lang/ref/Reference;->get()Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Lcom/miui/powercenter/nightcharge/ChargeProtectFragment;
    
    if-eqz v0, :temp_patch_end
    const-string v1, "sys.hack.temp"
    const-string v2, "25"
    invoke-static {{v1, v2}}, Landroid/os/SystemProperties;->get(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    
    new-instance v2, Ljava/lang/StringBuilder;
    invoke-direct {{v2}}, Ljava/lang/StringBuilder;-><init>()V
    invoke-virtual {{v2, v1}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    const-string v1, " \\\\u00b0C"
    invoke-virtual {{v2, v1}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {{v2}}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v1

    const-string v2, "reference_current_temp"
    invoke-virtual {{v0, v2}}, Landroidx/preference/PreferenceFragmentCompat;->findPreference(Ljava/lang/CharSequence;)Landroidx/preference/Preference;
    move-result-object v2
    if-eqz v2, :temp_patch_end
    check-cast v2, Lmiuix/preference/TextPreference;
    invoke-virtual {{v2, v1}}, Lmiuix/preference/TextPreference;->setText(Ljava/lang/String;)V
    :temp_patch_end
    """
        self.smali_patch(work_dir,
            file_path=str(target_file),
            method="handleMessage",
            regex_replace=(r"return-void", f"{reader_code}\n    return-void")
        )

    def _remove_battery_lock(self, work_dir: Path):
        """Corresponds to securitycenter_remove_battery_lock: Remove battery capacity limit"""
        remake_code = """
    .locals 5
    invoke-static {p0, p1}, Ljava/lang/Math;->max(II)I
    move-result p0
    return p0"""
        
        self.smali_patch(work_dir,
            seek_keyword="levelForceDown",
            remake=remake_code
        )

    def _add_capacity_info(self, work_dir: Path):
        """Corresponds to securitycenter_add_capacity_info_i18n: Inject detailed battery info"""
        self.logger.info("Injecting Battery Capacity Info (I18n)...")
        
        res_dir = self.xml.get_res_dir(work_dir)
        
        # 1. Inject String Resources
        # Default English
        self.xml.add_string(res_dir, "battery_design_capacity", "Design capacity")
        self.xml.add_string(res_dir, "battery_current_capacity", "Actual capacity")
        self.xml.add_string(res_dir, "battery_cycle_count", "Cycle count")
        self.xml.add_string(res_dir, "battery_unit_mah", " mAh")
        self.xml.add_string(res_dir, "battery_unit_count", " cycles")
        
        # Chinese (If zh-rCN exists, XmlUtils will handle)
        self.xml.add_string(res_dir, "battery_design_capacity", "设计容量", "zh-rCN")
        self.xml.add_string(res_dir, "battery_current_capacity", "当前容量", "zh-rCN")
        self.xml.add_string(res_dir, "battery_cycle_count", "循环次数", "zh-rCN")
        self.xml.add_string(res_dir, "battery_unit_mah", " mAh", "zh-rCN")
        self.xml.add_string(res_dir, "battery_unit_count", " 次", "zh-rCN")

        # 2. Construct complex Smali injection code
        # Template logic corresponds to I18N_TEMPLATE in Shell script
        # Use a Python generator function to save space
        def make_block(res_key, res_unit, path, divisor, label_id):
            return f"""
    # Block {label_id}
    new-instance v2, Lmiuix/preference/TextPreference;
    const/4 v3, 0x0
    invoke-direct {{v2, v0, v3}}, Lmiuix/preference/TextPreference;-><init>(Landroid/content/Context;Landroid/util/AttributeSet;)V
    invoke-virtual {{v1, v2}}, Landroidx/preference/PreferenceGroup;->addPreference(Landroidx/preference/Preference;)Z
    
    # Title
    invoke-virtual {{v0}}, Landroid/content/Context;->getResources()Landroid/content/res/Resources;
    move-result-object v3
    invoke-virtual {{v0}}, Landroid/content/Context;->getPackageName()Ljava/lang/String;
    move-result-object v4
    const-string v5, "string"
    const-string v1, "{res_key}"
    invoke-virtual {{v3, v1, v5, v4}}, Landroid/content/res/Resources;->getIdentifier(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)I
    move-result v1
    if-eqz v1, :def_t_{label_id}
    invoke-virtual {{v3, v1}}, Landroid/content/res/Resources;->getString(I)Ljava/lang/String;
    move-result-object v1
    goto :set_t_{label_id}
    :def_t_{label_id}
    const-string v1, "{res_key}"
    :set_t_{label_id}
    invoke-virtual {{v2, v1}}, Landroidx/preference/Preference;->setTitle(Ljava/lang/CharSequence;)V

    # Unit
    invoke-virtual {{v0}}, Landroid/content/Context;->getPackageName()Ljava/lang/String;
    move-result-object v4
    const-string v1, "{res_unit}"
    invoke-virtual {{v3, v1, v5, v4}}, Landroid/content/res/Resources;->getIdentifier(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)I
    move-result v1
    if-eqz v1, :def_u_{label_id}
    invoke-virtual {{v3, v1}}, Landroid/content/res/Resources;->getString(I)Ljava/lang/String;
    move-result-object v1
    goto :got_u_{label_id}
    :def_u_{label_id}
    const-string v1, ""
    :got_u_{label_id}

    # Value
    const-string v4, "{path}"
    new-instance v5, Ljava/io/File;
    invoke-direct {{v5, v4}}, Ljava/io/File;-><init>(Ljava/lang/String;)V
    invoke-virtual {{v5}}, Ljava/io/File;->exists()Z
    move-result v4
    if-eqz v4, :skip_{label_id}
    :try_{label_id}
    new-instance v4, Ljava/io/FileReader;
    invoke-direct {{v4, v5}}, Ljava/io/FileReader;-><init>(Ljava/io/File;)V
    new-instance v5, Ljava/io/BufferedReader;
    invoke-direct {{v5, v4}}, Ljava/io/BufferedReader;-><init>(Ljava/io/Reader;)V
    invoke-virtual {{v5}}, Ljava/io/BufferedReader;->readLine()Ljava/lang/String;
    move-result-object v4
    invoke-virtual {{v5}}, Ljava/io/BufferedReader;->close()V
    if-eqz v4, :skip_{label_id}
    invoke-virtual {{v4}}, Ljava/lang/String;->trim()Ljava/lang/String;
    move-result-object v4
    invoke-static {{v4}}, Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I
    move-result v4
    const/16 v5, {divisor}
    div-int/2addr v4, v5
    new-instance v3, Ljava/lang/StringBuilder;
    invoke-direct {{v3}}, Ljava/lang/StringBuilder;-><init>()V
    invoke-static {{v4}}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;
    move-result-object v4
    invoke-virtual {{v3, v4}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {{v3, v1}}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    invoke-virtual {{v3}}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v3
    invoke-virtual {{v2, v3}}, Lmiuix/preference/TextPreference;->setText(Ljava/lang/String;)V
    const/4 v3, 0x0
    invoke-virtual {{v2, v3}}, Landroidx/preference/Preference;->setSelectable(Z)V
    :try_end_{label_id}
    .catch Ljava/lang/Exception; {{:try_{label_id} .. :try_end_{label_id}}} :catch_{label_id}
    :catch_{label_id}
    :skip_{label_id}
    
    # Reset Group
    const-string v1, "preference_key_category_battery_info"
    invoke-virtual {{p0, v1}}, Landroidx/preference/PreferenceFragmentCompat;->findPreference(Ljava/lang/CharSequence;)Landroidx/preference/Preference;
    move-result-object v1
    check-cast v1, Landroidx/preference/PreferenceGroup;
    """

        header = """
    invoke-virtual {p0}, Landroidx/fragment/app/Fragment;->getContext()Landroid/content/Context;
    move-result-object v0
    const-string v2, "preference_key_category_battery_info"
    invoke-virtual {p0, v2}, Landroidx/preference/PreferenceFragmentCompat;->findPreference(Ljava/lang/CharSequence;)Landroidx/preference/Preference;
    move-result-object v1
    check-cast v1, Landroidx/preference/PreferenceGroup;
    if-nez v1, :start_custom_ui
    return-void
    :start_custom_ui
        """
        
        code = header
        code += make_block("battery_design_capacity", "battery_unit_mah", "/sys/class/power_supply/battery/charge_full_design", "0x3e8", "design")
        code += make_block("battery_current_capacity", "battery_unit_mah", "/sys/class/power_supply/battery/charge_full", "0x3e8", "actual")
        code += make_block("battery_cycle_count", "battery_unit_count", "/sys/class/power_supply/battery/cycle_count", "0x1", "cycle")

        self.smali_patch(work_dir,
            iname="ChargeProtectFragment.smali",
            seek_keyword="preference_key_category_battery_info",
            regex_replace=(r"return-void", f"{code}\n    return-void")
        )

    def _remove_intercept_timer(self, work_dir: Path):
        """Corresponds to securitycenter_remove_intercept_timer: Remove permission confirmation countdown"""
        self.logger.info("Removing Intercept Timer...")
        res_dir = self.xml.get_res_dir(work_dir)
        
        # 1. Find Resource ID ("确定（%d）")
        # Multi-step search: first find Name in strings.xml, then ID in public.xml
        # Simplified: iterate all strings.xml to find name with value="确定（%d）"
        str_name = None
        for f in res_dir.rglob("strings.xml"):
            content = f.read_text(encoding='utf-8', errors='ignore')
            # Match <string name="xxx">确定（%d）</string>
            m = re.search(r'<string name="([^"]+)">确定（%d）</string>', content)
            if m:
                str_name = m.group(1)
                break
        
        if not str_name:
            self.logger.warning("Resource string '确定（%d）' not found.")
            return

        str_id = self.xml.get_id(res_dir, str_name)
        if not str_id:
            self.logger.warning(f"ID for {str_name} not found.")
            return
        
        self.logger.info(f"Target ID: {str_id} ({str_name})")

        # 2. Find Smali file using this ID
        # Shell: grep -l -r ID | grep initData
        target_smali = None
        for f in work_dir.rglob("*.smali"):
            try:
                c = f.read_text(encoding='utf-8', errors='ignore')
                if str_id in c and "initData" in c:
                    target_smali = f
                    break
            except: pass
        
        if not target_smali:
            self.logger.warning("Timer method not found in smali.")
            return

        # 3. Locate specific method (returns Int)
        # Shell Logic: Find ID line -> search upwards for invoke-virtual ... ()I
        content = target_smali.read_text(encoding='utf-8')
        
        # Implementation of grep logic in Python:
        # 1. Find initData method body
        init_match = re.search(r"\.method.*initData.*?\.end method", content, re.DOTALL)
        if init_match:
            init_body = init_match.group(0)
            # 2. Find ID in initData
            if str_id in init_body:
                # 3. Find invoke-virtual {p0} ... ()I before ID
                # Python re cannot lookbehind variable length, so split
                pre_id_code = init_body.split(str_id)[0]
                invokes = re.findall(r"invoke-virtual \{p0\}, (L.*?;->(.*)\(\)I)", pre_id_code)
                if invokes:
                    # Take the last one
                    full_sig, method_name = invokes[-1]
                    self.logger.info(f"Target Timer Method: {method_name}")
                    
                    # 4. Patch the method
                    self.smali_patch(work_dir,
                        file_path=str(target_smali),
                        method=method_name,
                        return_type="I",
                        remake=".locals 1\n    const/4 v0, 0x0\n    return v0"
                    )
