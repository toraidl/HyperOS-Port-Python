import json
import os
import re
import shutil
import logging
import concurrent.futures
from pathlib import Path

import tempfile
import urllib
import zipfile
from src.utils.shell import ShellRunner
import urllib.request
from urllib.error import URLError
import subprocess

from src.utils.smalikit import SmaliKit

class SmaliArgs:
    def __init__(self, **kwargs):
        self.path = None
        self.file_path = None
        self.method = None
        self.seek_keyword = None
        self.iname = None
        self.remake = None
        self.replace_in_method = None
        self.regex_replace = None
        self.delete_in_method = None
        self.delete_method = False
        self.after_line = None
        self.before_line = None
        self.insert_line = None
        self.recursive = False
        self.return_type = None
        
        self.__dict__.update(kwargs)

class SystemModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("Modifier")
        self.shell = ShellRunner()
        
        self.bin_dir = Path("bin").resolve()
        self.apktool = self.bin_dir / "apktool.jar"
        
        self.temp_dir = self.ctx.target_dir.parent / "temp"

    def run(self):
        self.logger.info("Starting System Modification...")
        
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            self.android_version = int(self.ctx.port.get_prop("ro.build.version.release", "14"))
        except:
            self.android_version = 14
        
        # Order matters!
        self._process_replacements()
        self._migrate_configs()
        # Unlock features AFTER config migration, otherwise changes to XMLs are lost
        self._unlock_device_features()
        
        self._fix_vndk_apex()
        self._fix_vintf_manifest()
        
        # 7. Apply EU Localization (if enabled/bundle provided)
        if getattr(self.ctx, "is_port_eu_rom", False) and getattr(self.ctx, "eu_bundle", None):
            self._apply_eu_localization()

        self.logger.info("System Modification Completed.")

    def _process_replacements(self):
        """
        Execute file/directory replacements defined in replacements.json.
        """
        replacements = self._load_replacement_config()
        if not replacements:
            return

        self.logger.info("Processing file replacements...")
        
        stock_root = self.ctx.stock.extracted_dir
        target_root = self.ctx.target_dir

        for rule in replacements:
            desc = rule.get("description", "Unknown Rule")
            rtype = rule.get("type", "file")
            search_path = rule.get("search_path", "")
            match_mode = rule.get("match_mode", "exact")
            ensure_exists = rule.get("ensure_exists", False)
            files = rule.get("files", [])

            self.logger.info(f"Applying rule: {desc}")

            # Define search roots
            rule_stock_root = stock_root / search_path
            rule_target_root = target_root / search_path

            if not rule_stock_root.exists():
                self.logger.debug(f"Source search path not found: {rule_stock_root}")
                continue

            for pattern in files:
                # Find matching items in Source (Stock ROM)
                sources = []
                if match_mode == "glob":
                    sources = list(rule_stock_root.glob(pattern))
                elif match_mode == "recursive":
                    sources = list(rule_stock_root.rglob(pattern))
                else:
                    # exact
                    exact_file = rule_stock_root / pattern
                    if exact_file.exists():
                        sources = [exact_file]
                
                if not sources:
                    self.logger.debug(f"No source items found for pattern: {pattern}")
                    continue

                for src_item in sources:
                    # Calculate relative path to apply to target
                    rel_name = src_item.name
                    target_item = rule_target_root / rel_name
                    
                    # Logic: 
                    # If ensure_exists=True: Copy even if target doesn't have it (Force Add)
                    # If ensure_exists=False: Copy ONLY if target already has it (Replace)
                    
                    should_copy = False
                    
                    if match_mode == "glob":
                        # For glob, we might need to find corresponding target by name
                        # If target_item exists, we replace.
                        if target_item.exists():
                            should_copy = True
                        elif ensure_exists:
                            should_copy = True
                    else:
                        # Exact match
                        if target_item.exists():
                            should_copy = True
                        elif ensure_exists:
                            should_copy = True
                            
                    if should_copy:
                        self.logger.info(f"  Replacing/Adding: {rel_name}")
                        
                        # Prepare target directory
                        if not target_item.parent.exists():
                            target_item.parent.mkdir(parents=True, exist_ok=True)
                            
                        # Remove existing target
                        if target_item.exists():
                            if target_item.is_dir():
                                shutil.rmtree(target_item)
                            else:
                                target_item.unlink()
                        
                        # Copy
                        if src_item.is_dir():
                            shutil.copytree(src_item, target_item, symlinks=True, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src_item, target_item)
                    else:
                        self.logger.debug(f"  Skipping {rel_name} (Target missing and ensure_exists=False)")

    def _load_replacement_config(self):
        """
        Load replacements.json from common and device folder.
        Strategy: Append (Common + Device)
        """
        replacements = []
        
        # 1. Common
        common_cfg = Path("devices/common/replacements.json")
        if common_cfg.exists():
            try:
                with open(common_cfg, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        replacements.extend(data)
                        self.logger.info("Loaded common replacements.")
            except Exception as e:
                self.logger.error(f"Failed to load common replacements: {e}")

        # 2. Device (Append)
        device_cfg = Path(f"devices/{self.ctx.stock_rom_code}/replacements.json")
        if device_cfg.exists():
            try:
                with open(device_cfg, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        replacements.extend(data)
                        self.logger.info(f"Loaded device replacements for {self.ctx.stock_rom_code}.")
            except Exception as e:
                self.logger.error(f"Failed to load device replacements: {e}")
        
        return replacements

    def _apply_eu_localization(self):
        bundle_path = Path(self.ctx.eu_bundle)
        if not bundle_path.exists():
            self.logger.warning(f"EU Bundle not found at {bundle_path}, skipping localization.")
            return

        self.logger.info(f"Applying EU Localization Bundle from {bundle_path}...")
        
        with tempfile.TemporaryDirectory(prefix="eu_bundle_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            
            # 1. Extract Bundle
            try:
                with zipfile.ZipFile(bundle_path, 'r') as z:
                    z.extractall(tmp_path)
            except Exception as e:
                self.logger.error(f"Failed to extract EU bundle: {e}")
                return

            # 2. Iterate and Smart Replace
            # Walk through extracted files to find APKs
            for apk_file in tmp_path.rglob("*.apk"):
                # Get package name using aapt2
                pkg_name = self._get_package_name(apk_file)
                if not pkg_name:
                    continue
                
                # relative path inside bundle (e.g. product/app/MiuiCamera/MiuiCamera.apk)
                # We need to determine the root relative to the bundle structure.
                # Assuming bundle structure mirrors system root (e.g. system/..., product/...)
                
                # Find matching app in Target ROM
                # Scan common app directories in target
                found_in_target = False
                target_roots = [
                    self.ctx.target_dir / "system/app",
                    self.ctx.target_dir / "system/priv-app",
                    self.ctx.target_dir / "product/app",
                    self.ctx.target_dir / "product/priv-app",
                    self.ctx.target_dir / "system_ext/app",
                    self.ctx.target_dir / "system_ext/priv-app"
                ]
                
                for root in target_roots:
                    if not root.exists(): continue
                    
                    # Search recursively in this app root
                    for target_apk in root.rglob("*.apk"):
                        target_pkg = self._get_package_name(target_apk)
                        if target_pkg == pkg_name:
                            # FOUND MATCH!
                            app_dir = target_apk.parent
                            self.logger.info(f"Replacing EU App: {pkg_name}")
                            self.logger.info(f"  - Removing: {app_dir}")
                            
                            # Delete old dir
                            shutil.rmtree(app_dir)
                            
                            # Calculate new destination
                            # We place the new app in the SAME location structure as the bundle
                            # relative_path = apk_file.relative_to(tmp_path)
                            # dest_path = self.ctx.target_dir / relative_path
                            
                            # Actually, we should probably place it where the old one was to be safe?
                            # OR trust the bundle structure. 
                            # If we trust bundle structure, we just copy.
                            # But we must delete the old one first to avoid duplicates if path differs.
                            
                            found_in_target = True
                            break
                    if found_in_target: break
                
                if not found_in_target:
                    self.logger.info(f"Adding new EU App: {pkg_name}")

            # 3. Merging Bundle Files
            # Now that we've cleaned up conflicts, simply overlay the bundle
            self.logger.info("Merging EU Bundle files into Target ROM...")
            shutil.copytree(tmp_path, self.ctx.target_dir, dirs_exist_ok=True)

    def _get_package_name(self, apk_path):
        try:
            # aapt2 dump packagename <apk>
            # Output: package: name='com.android.chrome'
            cmd = [str(self.ctx.tools.aapt2), "dump", "packagename", str(apk_path)]
            result = self.shell.run(cmd, capture_output=True, check=False)
            if result.returncode == 0:
                output = result.stdout.strip()
                # Parse "package: name='com.foo.bar'"
                if "package: name=" in output:
                    return output.split("'")[1]
            return None
        except Exception:
            return None

    def _unlock_device_features(self):
        """
        Unlock device features based on JSON configuration (Common + Device specific)
        """
        self.logger.info("Unlocking device features (AOD, AI Display, MEMC)...")
        
        # 1. Load Configuration
        config = self._load_feature_config()
        if not config:
            return

        # 2. Apply XML Features
        xml_features = config.get("xml_features", {})
        if xml_features:
            self._apply_xml_features(xml_features)

        # 3. Apply Build Props
        build_props = config.get("build_props", {})
        if build_props:
            self._apply_build_props(build_props)
            
        # 4. Apply EU Localization Props (if enabled)
        # Condition: Auto-detected EU ROM OR explicit enable_eu_localization in features.json
        enable_eu_loc = config.get("enable_eu_localization", False) or getattr(self.ctx, "is_port_eu_rom", False)
        
        if enable_eu_loc:
            self.logger.info("Enabling EU Localization properties...")
            eu_cfg_path = Path("devices/common/eu_localization.json")
            if eu_cfg_path.exists():
                try:
                    with open(eu_cfg_path, 'r') as f:
                        eu_config = json.load(f)
                    eu_props = eu_config.get("build_props", {})
                    self._apply_build_props(eu_props)
                except Exception as e:
                    self.logger.error(f"Failed to apply EU localization props: {e}")

    def _load_feature_config(self):
        config = {}
        
        # Load Common Config
        common_cfg = Path("devices/common/features.json")
        if common_cfg.exists():
            try:
                with open(common_cfg, 'r') as f:
                    config = json.load(f)
                self.logger.info("Loaded common features config.")
            except Exception as e:
                self.logger.error(f"Failed to load common features: {e}")

        # Load Device Config (Override)
        device_cfg = Path(f"devices/{self.ctx.stock_rom_code}/features.json")
        if device_cfg.exists():
            try:
                with open(device_cfg, 'r') as f:
                    device_config = json.load(f)
                
                # Deep merge logic
                for key, value in device_config.items():
                    if isinstance(value, dict) and key in config:
                        config[key].update(value)
                    else:
                        config[key] = value
                self.logger.info(f"Loaded device features config for {self.ctx.stock_rom_code}.")
            except Exception as e:
                self.logger.error(f"Failed to load device features: {e}")
        
        return config

    def _apply_xml_features(self, features):
        feat_dir = self.ctx.target_dir / "product/etc/device_features"
        if not feat_dir.exists():
            self.logger.warning("device_features directory not found.")
            return

        # Target file: usually matches stock code, or just find any XML
        xml_file = feat_dir / f"{self.ctx.stock_rom_code}.xml"
        if not xml_file.exists():
            # Fallback: try finding any XML in the folder
            try:
                xml_file = next(feat_dir.glob("*.xml"))
            except StopIteration:
                self.logger.warning("No device features XML found.")
                return

        self.logger.info(f"Modifying features in {xml_file.name}...")
        content = xml_file.read_text(encoding='utf-8')
        
        modified = False
        for name, value in features.items():
            str_value = str(value).lower() # true/false
            
            # Check existence
            # Regex to find <bool name="feature_name">...</bool>
            pattern = re.compile(rf'<bool name="{re.escape(name)}">.*?</bool>')
            
            if pattern.search(content):
                # Update existing
                new_tag = f'<bool name="{name}">{str_value}</bool>'
                new_content = pattern.sub(new_tag, content)
                if new_content != content:
                    content = new_content
                    modified = True
                    self.logger.debug(f"Updated feature: {name} = {str_value}")
            else:
                # Insert new (before </features>)
                if "</features>" in content:
                    new_tag = f'    <bool name="{name}">{str_value}</bool>\n</features>'
                    content = content.replace("</features>", new_tag)
                    modified = True
                    self.logger.debug(f"Added feature: {name} = {str_value}")
        
        if modified:
            xml_file.write_text(content, encoding='utf-8')

    def _apply_build_props(self, props_map):
        for partition, props in props_map.items():
            if partition == "vendor":
                prop_file = self.ctx.target_dir / "vendor/build.prop"
            elif partition == "product":
                prop_file = self.ctx.target_dir / "product/etc/build.prop"
            else:
                continue
            
            if not prop_file.exists():
                continue
                
            content = prop_file.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
            new_lines = []
            
            # Simple parsing to avoid duplicates
            existing_keys = set()
            for line in lines:
                if "=" in line and not line.strip().startswith("#"):
                    existing_keys.add(line.split("=")[0].strip())
                new_lines.append(line)
            
            appended = False
            for key, value in props.items():
                if key not in existing_keys:
                    new_lines.append(f"{key}={value}")
                    self.logger.debug(f"Appended prop to {partition}: {key}={value}")
                    appended = True
                # If we wanted to update existing props, we'd need more complex logic here
            
            if appended:
                prop_file.write_text("\n".join(new_lines) + "\n", encoding='utf-8')

    def _find_file_recursive(self, root_dir: Path, filename: str) -> Path | None:
        if not root_dir.exists(): return None
        try:
            return next(root_dir.rglob(filename))
        except StopIteration:
            return None

    def _find_dir_recursive(self, root_dir: Path, dirname: str) -> Path | None:
        if not root_dir.exists(): return None
        for p in root_dir.rglob(dirname):
            if p.is_dir() and p.name == dirname:
                return p
        return None

    def _migrate_configs(self):
        target_product = self.ctx.target_dir / "product"
        stock_product = self.ctx.stock.extracted_dir / "product"
        
        target_disp = target_product / "etc/displayconfig"
        stock_disp = stock_product / "etc/displayconfig"
        
        if target_disp.exists():
            for f in target_disp.glob("display_id*.xml"):
                f.unlink()
        
        if stock_disp.exists():
            target_disp.mkdir(parents=True, exist_ok=True)
            for f in stock_disp.glob("display_id*.xml"):
                shutil.copy2(f, target_disp)
            self.logger.info("Migrated displayconfig.")

        target_feat = target_product / "etc/device_features"
        stock_feat = stock_product / "etc/device_features"
        
        if target_feat.exists():
            shutil.rmtree(target_feat)
        
        if stock_feat.exists():
            shutil.copytree(stock_feat, target_feat, dirs_exist_ok=True)
            self.logger.info("Migrated device_features.")
            
        stock_json = stock_product / "etc/device_info.json"
        target_json = target_product / "etc/device_info.json"
        if stock_json.exists():
             shutil.copy2(stock_json, target_json)

    def _apktool_decode(self, apk_path: Path, out_dir: Path):
        self.shell.run_java_jar(self.apktool, ["d", str(apk_path), "-o", str(out_dir), "-f"])
    
    def _apktool_build(self, src_dir: Path, out_apk: Path):
        self.shell.run_java_jar(self.apktool, ["b", str(src_dir), "-o", str(out_apk),"-f"])

    def _fix_vndk_apex(self):
        vndk_version = self.ctx.stock.get_prop("ro.vndk.version")
        
        if not vndk_version:
             for prop in (self.ctx.stock.extracted_dir / "vendor").rglob("*.prop"):
                 try:
                     with open(prop, errors='ignore') as f:
                         for line in f:
                             if "ro.vndk.version=" in line:
                                 vndk_version = line.split("=")[1].strip()
                                 break
                 except: pass
                 if vndk_version: break
        
        if not vndk_version: return

        apex_name = f"com.android.vndk.v{vndk_version}.apex"
        stock_apex = self._find_file_recursive(self.ctx.stock.extracted_dir / "system_ext/apex", apex_name)
        target_apex_dir = self.ctx.target_dir / "system_ext/apex"
        
        if stock_apex and target_apex_dir.exists():
            target_file = target_apex_dir / apex_name
            if not target_file.exists():
                self.logger.info(f"Copying missing VNDK Apex: {apex_name}")
                shutil.copy2(stock_apex, target_file)
    
    def _apply_device_overrides(self):
        base_code = self.ctx.stock_rom_code
        port_ver = self.ctx.port_android_version
        
        override_src = Path(f"devices/{base_code}/override/{port_ver}").resolve()
        
        if not override_src.exists() or not override_src.is_dir():
            self.logger.warning(f"Device overlay dir not found: {override_src}")
            return

        self.logger.info(f"Applying device overrides from: {override_src}")

        has_nfc_override = False
        for f in override_src.rglob("*.apk"):
            name = f.name.lower()
            if name.startswith("nqnfcnci") or name.startswith("nfc_st"):
                has_nfc_override = True
                break
        
        if has_nfc_override:
            self.logger.info("Detected NFC override, cleaning old NFC directories in target...")
            for p in self.ctx.target_dir.rglob("*"):
                if p.is_dir():
                    name = p.name.lower()
                    if name.startswith("nqnfcnci") or name.startswith("nfc_st"):
                        self.logger.info(f"Removing old NFC dir: {p}")
                        shutil.rmtree(p)

        self.logger.info("Copying override files...")
        try:
            shutil.copytree(override_src, self.ctx.target_dir, dirs_exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to copy overrides: {e}")

    def _fix_vintf_manifest(self):
        self.logger.info("Checking VINTF manifest for VNDK version...")

        vndk_version = self.ctx.stock.get_prop("ro.vndk.version")
        if not vndk_version:
            vendor_prop = self.ctx.target_dir / "vendor/build.prop"
            if vendor_prop.exists():
                try:
                    content = vendor_prop.read_text(encoding='utf-8', errors='ignore')
                    match = re.search(r"ro\.vndk\.version=(.*)", content)
                    if match:
                        vndk_version = match.group(1).strip()
                except: pass

        if not vndk_version:
            self.logger.warning("Could not determine VNDK version, skipping VINTF fix.")
            return

        self.logger.info(f"Target VNDK Version: {vndk_version}")

        target_xml = self._find_file_recursive(self.ctx.target_dir / "system_ext", "manifest.xml")
        if not target_xml:
            self.logger.warning("manifest.xml not found.")
            return

        original_content = target_xml.read_text(encoding='utf-8')
        
        if f"<version>{vndk_version}</version>" in original_content:
            self.logger.info(f"VNDK {vndk_version} already exists in manifest. Skipping.")
            return

        new_block = f"""    <vendor-ndk>
        <version>{vndk_version}</version>
    </vendor-ndk>"""

        if "</manifest>" in original_content:
            new_content = original_content.replace("</manifest>", f"{new_block}\n</manifest>")
            
            target_xml.write_text(new_content, encoding='utf-8')
            self.logger.info(f"Injected VNDK {vndk_version} into {target_xml.name} (Text Mode)")
        else:
            self.logger.error("Invalid manifest.xml: No </manifest> tag found.")

class FrameworkModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("FrameworkModifier")
        self.shell = ShellRunner()
        self.bin_dir = Path("bin").resolve()
        
        self.apktool_path = self.bin_dir / "apktool" / "apktool"
        self.apkeditor_path = self.bin_dir / "APKEditor.jar"
        self.baksmali_path = self.bin_dir / "baksmali.jar"
        
        self.RETRUN_TRUE = ".locals 1\n    const/4 v0, 0x1\n    return v0"
        self.RETRUN_FALSE = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        self.REMAKE_VOID = ".locals 0\n    return-void"
        self.INVOKE_TRUE = "invoke-static {}, Lcom/android/internal/util/HookHelper;->RETURN_TRUE()Z"
        self.PRELOADS_SHAREDUIDS = ".locals 1\n    invoke-static {}, Lcom/android/internal/util/HookHelper;->RETURN_TRUE()Z\n    move-result v0\n    sput-boolean v0, Lcom/android/server/pm/ReconcilePackageUtils;->ALLOW_NON_PRELOADS_SYSTEM_SHAREDUIDS:Z\n    return-void"

        self.temp_dir = self.ctx.target_dir.parent / "temp_modifier"

    def run(self):
        self.logger.info("Starting System Modification...")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            futures.append(executor.submit(self._mod_miui_services))
            futures.append(executor.submit(self._mod_services))
            futures.append(executor.submit(self._mod_framework))
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Framework modification failed: {e}")

        self._inject_xeu_toolbox()
        self.logger.info("System Modification Completed.")

    def _run_smalikit(self, **kwargs):
        args = SmaliArgs(**kwargs)
        patcher = SmaliKit(args)
        target = args.file_path if args.file_path else args.path
        if target:
            patcher.walk_and_patch(target)

    def _apkeditor_decode(self, jar_path, out_dir):
        self.shell.run_java_jar(self.apkeditor_path, ["d", "-f", "-i", str(jar_path), "-o", str(out_dir)])

    def _apkeditor_build(self, src_dir, out_jar):
        self.shell.run_java_jar(self.apkeditor_path, ["b", "-f", "-i", str(src_dir), "-o", str(out_jar)])

    def _find_file(self, root, name_pattern):
        for p in Path(root).rglob(name_pattern):
            if p.is_file(): return p
        return None

    def _replace_text_in_file(self, file_path, old, new):
        if not file_path or not file_path.exists():
            return
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        if old in content:
            new_content = content.replace(old, new)
            file_path.write_text(new_content, encoding='utf-8')
            self.logger.info(f"Patched {file_path.name}: {old[:20]}... -> {new[:20]}...")

    def _mod_miui_services(self):
        jar_path = self._find_file(self.ctx.target_dir, "miui-services.jar")
        if not jar_path: return

        self.logger.info(f"Modifying {jar_path.name}...")
        work_dir = self.temp_dir / "miui-services"
        self._apkeditor_decode(jar_path, work_dir)

        if getattr(self.ctx, "is_port_eu_rom", False):
            fuc_body = ".locals 1\n    invoke-direct {p0}, Lcom/android/server/SystemServerStub;-><init>()V\n    return-void"
            self._run_smalikit(
                path=str(work_dir),
                iname="SystemServerImpl.smali",
                method="<init>()V",
                remake=fuc_body
            )

        remake_void = ".locals 0\n    return-void"
        remake_false = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceImpl.smali", method="verifyIsolationViolation", remake=remake_void, recursive=True)
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceImpl.smali", method="canBeUpdate", remake=remake_void, recursive=True)
        
        patches = [
            ("com/android/server/am/BroadcastQueueModernStubImpl.smali", [
                ('sget-boolean v2, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v2, 0x1')
            ]),
            ("com/android/server/am/ActivityManagerServiceImpl.smali", [
                ('sget-boolean v1, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v1, 0x1'),
                ('sget-boolean v4, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v4, 0x1')
            ]),
            ("com/android/server/am/ProcessManagerService.smali", [
                ('sget-boolean v0, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v0, 0x1')
            ]),
            ("com/android/server/am/ProcessSceneCleaner.smali", [
                ('sget-boolean v4, Lmiui/os/Build;->IS_INTERNATIONAL_BUILD:Z', 'const/4 v0, 0x1')
            ]),
        ]

        for rel_path, rules in patches:
            target_smali = self._find_file(work_dir, Path(rel_path).name)
            if target_smali:
                for old_str, new_str in rules:
                    self._replace_text_in_file(target_smali, old_str, new_str)

        self._run_smalikit(path=str(work_dir), iname="WindowManagerServiceImpl.smali", method="notAllowCaptureDisplay(Lcom/android/server/wm/RootWindowContainer;I)Z", remake=remake_false, recursive=True)

        self._apkeditor_build(work_dir, jar_path)

    def _mod_services(self):
        jar_path = self._find_file(self.ctx.target_dir, "services.jar")
        if not jar_path: return

        self.logger.info(f"Modifying {jar_path.name}...")
        work_dir = self.temp_dir / "services"
        shutil.copy2(jar_path, self.temp_dir / "services.jar.bak")
        self._apkeditor_decode(jar_path, work_dir)

        remake_void = ".locals 0\n    return-void"
        remake_false = ".locals 1\n    const/4 v0, 0x0\n    return v0"
        remake_true = ".locals 1\n    const/4 v0, 0x1\n    return v0"
        
        self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceUtils.smali", method="checkDowngrade", remake=remake_void, recursive=True)
        for m in ["matchSignaturesCompat", "matchSignaturesRecover", "matchSignatureInSystem", "verifySignatures"]:
            self._run_smalikit(path=str(work_dir), iname="PackageManagerServiceUtils.smali", method=m, remake=remake_false)

        self._run_smalikit(path=str(work_dir), iname="KeySetManagerService.smali", method="checkUpgradeKeySetLocked", remake=remake_true)
        
        self._run_smalikit(path=str(work_dir), iname="VerifyingSession.smali", method="isVerificationEnabled", remake=remake_false)
        
        self._apkeditor_build(work_dir, jar_path)

    def _find_file_recursive(self, root_dir: Path, filename: str) -> Path | None:
        if not root_dir.exists(): return None
        try:
            return next(root_dir.rglob(filename))
        except StopIteration:
            return None

    def _find_dir_recursive(self, root_dir: Path, dirname: str) -> Path | None:
        if not root_dir.exists(): return None
        for p in root_dir.rglob(dirname):
            if p.is_dir() and p.name == dirname:
                return p
        return None

    def _mod_framework(self):
        jar = self._find_file_recursive(self.ctx.target_dir, "framework.jar")
        if not jar: return
        self.logger.info(f"Modifying {jar.name} (PropsHook, PIF & SignBypass)...")
        
        wd = self.temp_dir / "framework"
        self.shell.run_java_jar(self.apkeditor_path, ["d", "-f", "-i", str(jar), "-o", str(wd), "-no-dex-debug"])

        props_hook_zip = Path("devices/common/PropsHook.zip")
        if props_hook_zip.exists():
            self.logger.info("Injecting PropsHook...")
            hook_tmp = self.temp_dir / "PropsHook"
            with zipfile.ZipFile(props_hook_zip, 'r') as z:
                z.extractall(hook_tmp)
            
            classes_dex = hook_tmp / "classes.dex"
            if classes_dex.exists():
                classes_out = hook_tmp / "classes"
                self.shell.run_java_jar(self.baksmali_path, ["d", str(classes_dex), "-o", str(classes_out)])
                
                self._copy_to_next_classes(wd, classes_out)

        self.logger.info("Applying Signature Bypass Patches...")
        
        self._run_smalikit(path=str(wd), iname="StrictJarVerifier.smali", method="verifyMessageDigest([B[B)Z", remake=self.RETRUN_TRUE)
        self._run_smalikit(path=str(wd), iname="StrictJarVerifier.smali", 
                           method="<init>(Ljava/lang/String;Landroid/util/jar/StrictJarManifest;Ljava/util/HashMap;Z)V", 
                           before_line=["iput-boolean p4, p0, Landroid/util/jar/StrictJarVerifier;->signatureSchemeRollbackProtectionsEnforced:Z", "const/4 p4, 0x0"])

        targets = [
            ("ApkSigningBlockUtils.smali", "verifyIntegrityFor1MbChunkBasedAlgorithm"),
            ("ApkSigningBlockUtils.smali", "verifyProofOfRotationStruct"),
            ("ApkSignatureSchemeV2Verifier.smali", "verifySigner"),
            ("ApkSignatureSchemeV3Verifier.smali", "verifySigner"),
            ("ApkSignatureSchemeV4Verifier.smali", "verifySigner"),
        ]
        s1 = "Ljava/security/MessageDigest;->isEqual([B[B)Z"
        s2 = "Ljava/security/Signature;->verify([B)Z"
        
        for smali_file, method in targets:
             self._run_smalikit(path=str(wd), iname=smali_file, method=method, after_line=[s1, self.INVOKE_TRUE], recursive=True)
             self._run_smalikit(path=str(wd), iname=smali_file, method=method, after_line=[s2, self.INVOKE_TRUE], recursive=True)

        for m in ["checkCapability", "checkCapabilityRecover", "hasCommonAncestor", "signaturesMatchExactly"]:
            self._run_smalikit(path=str(wd), iname="PackageParser$SigningDetails.smali", method=m, remake=self.RETRUN_TRUE, recursive=True)
            self._run_smalikit(path=str(wd), iname="SigningDetails.smali", method=m, remake=self.RETRUN_TRUE, recursive=True)

        self._run_smalikit(path=str(wd), iname="AssetManager.smali", method="containsAllocatedTable", remake=self.RETRUN_FALSE)

        self._run_smalikit(path=str(wd), iname="StrictJarFile.smali", 
                           method="<init>(Ljava/lang/String;Ljava/io/FileDescriptor;ZZ)V", 
                           after_line=["move-result-object v6", "const/4 v6, 0x1"])

        self._run_smalikit(path=str(wd), iname="ApkSignatureVerifier.smali", method="getMinimumSignatureSchemeVersionForTargetSdk", remake=self.RETRUN_TRUE)

        pif_zip = Path("devices/common/pif_patch.zip")
        if pif_zip.exists():
            self._apply_pif_patch(wd, pif_zip)
        else:
            self.logger.warning("pif_patch.zip not found, skipping PIF injection.")

        target_file = self._find_file_recursive(wd, "PendingIntent.smali")
        if target_file:
            hook_code = "\n    # [AutoCopy Hook]\n    invoke-static {p0, p2}, Lcom/android/internal/util/HookHelper;->onPendingIntentGetActivity(Landroid/content/Context;Landroid/content/Intent;)V"
            self._run_smalikit(file_path=str(target_file), method="getActivity(Landroid/content/Context;ILandroid/content/Intent;I)", insert_line=["2", hook_code])
            self._run_smalikit(file_path=str(target_file), method="getActivity(Landroid/content/Context;ILandroid/content/Intent;ILandroid/os/Bundle;)", insert_line=["2", hook_code])

        self._integrate_custom_platform_key(wd)

        # ==========================================
        # 6. 注入 HookHelper 实现 (AutoCopy)
        # ==========================================
        self._inject_hook_helper_methods(wd)

        self._apkeditor_build(wd, jar)

    def _inject_hook_helper_methods(self, work_dir):
        """
        注入 HookHelper 的额外方法 (AutoCopy 等)
        """
        hook_helper = self._find_file_recursive(work_dir, "HookHelper.smali")
        if not hook_helper:
            self.logger.warning("HookHelper.smali not found, creating new one...")
            return

        self.logger.info(f"Injecting implementation into {hook_helper.name}...")
        
        # 定义 Smali 代码
        smali_code = r"""
.method public static onPendingIntentGetActivity(Landroid/content/Context;Landroid/content/Intent;)V
    .locals 5

    .line 100
    if-eqz p1, :cond_end

    # Check for extras
    invoke-virtual {p1}, Landroid/content/Intent;->getExtras()Landroid/os/Bundle;
    move-result-object v0
    if-nez v0, :cond_check_clip

    goto :cond_end

    :cond_check_clip
    # Try to find "sms_body" or typical keys
    const-string v1, "android.intent.extra.TEXT"
    invoke-virtual {v0, v1}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    
    if-nez v1, :cond_check_body
    const-string v1, "sms_body"
    invoke-virtual {v0, v1}, Landroid/os/Bundle;->getString(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1

    :cond_check_body
    if-nez v1, :cond_scan_match
    goto :cond_end

    :cond_scan_match
    # Now v1 is the content string. Run Regex.
    # Regex: (?<![0-9])([0-9]{4,6})(?![0-9])
    
    const-string v2, "(?<![0-9])([0-9]{4,6})(?![0-9])"
    invoke-static {v2}, Ljava/util/regex/Pattern;->compile(Ljava/lang/String;)Ljava/util/regex/Pattern;
    move-result-object v2
    invoke-virtual {v2, v1}, Ljava/util/regex/Pattern;->matcher(Ljava/lang/CharSequence;)Ljava/util/regex/Matcher;
    move-result-object v2
    
    invoke-virtual {v2}, Ljava/util/regex/Matcher;->find()Z
    move-result v3
    if-eqz v3, :cond_end
    
    # Found match! Group 1 is the code
    const/4 v3, 0x1
    invoke-virtual {v2, v3}, Ljava/util/regex/Matcher;->group(I)Ljava/lang/String;
    move-result-object v2
    
    if-eqz v2, :cond_end
    
    # Copy to Clipboard
    const-string v3, "clipboard"
    invoke-virtual {p0, v3}, Landroid/content/Context;->getSystemService(Ljava/lang/String;)Ljava/lang/Object;
    move-result-object v3
    check-cast v3, Landroid/content/ClipboardManager;
    
    if-eqz v3, :cond_end
    
    # ClipData.newPlainText("Verification Code", code)
    const-string v4, "Verification Code"
    invoke-static {v4, v2}, Landroid/content/ClipData;->newPlainText(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Landroid/content/ClipData;
    move-result-object v2
    
    invoke-virtual {v3, v2}, Landroid/content/ClipboardManager;->setPrimaryClip(Landroid/content/ClipData;)V
    
    :cond_end
    return-void
.end method
"""
        # Append method to HookHelper.smali
        content = hook_helper.read_text(encoding='utf-8')
        if "onPendingIntentGetActivity" not in content:
            with open(hook_helper, "a", encoding="utf-8") as f:
                f.write(smali_code)
                
            self.logger.info("Added onPendingIntentGetActivity to HookHelper.")
        else:
            self.logger.info("onPendingIntentGetActivity already exists.")

    # --------------------------------------------------------------------------
    # PIF Patch 逻辑 (模拟 patches.sh)
        # --------------------------------------------------------------------------
    def _apply_pif_patch(self, work_dir, pif_zip):
        self.logger.info("Applying PIF Patch (Instrumentation, KeyStoreSpi, AppPM)...")
        
        temp_pif = self.temp_dir / "pif_classes"
        with zipfile.ZipFile(pif_zip, 'r') as z:
            z.extractall(temp_pif)
        self._copy_to_next_classes(work_dir, temp_pif / "classes")
        
        self.logger.info(f"Merging files from {temp_pif} to {self.ctx.target_dir}...")
        
        for item in temp_pif.iterdir():
            if item.name == "classes":
                continue
            
            target_path = self.ctx.target_dir / item.name
            
            self.logger.info(f"  Merging: {item.name} -> {target_path}")
            
            if item.is_dir():
                shutil.copytree(item, target_path, symlinks=True, dirs_exist_ok=True)
            else:
                if target_path.exists() or os.path.islink(target_path):
                    if target_path.is_dir(): shutil.rmtree(target_path)
                    else: os.unlink(target_path)
                
                shutil.copy2(item, target_path, follow_symlinks=False)

        inst_smali = self._find_file_recursive(work_dir, "Instrumentation.smali")
        if inst_smali:
            content = inst_smali.read_text(encoding='utf-8', errors='ignore')
            
            method1 = "newApplication(Ljava/lang/ClassLoader;Ljava/lang/String;Landroid/content/Context;)Landroid/app/Application;"
            if method1 in content:
                reg = self._extract_register_from_invoke(content, method1, "Landroid/app/Application;->attach(Landroid/content/Context;)V", arg_index=1)
                if reg:
                    patch_code = f"    invoke-static {{{reg}}}, Lcom/android/internal/util/PropsHookUtils;->setProps(Landroid/content/Context;)V\n    invoke-static {{{reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onNewApplication(Landroid/content/Context;)V"
                    self._run_smalikit(file_path=str(inst_smali), method=method1, before_line=["return-object", patch_code])

            method2 = "newApplication(Ljava/lang/Class;Landroid/content/Context;)Landroid/app/Application;"
            if method2 in content:
                reg = self._extract_register_from_invoke(content, method2, "Landroid/app/Application;->attach(Landroid/content/Context;)V", arg_index=1)
                if reg:
                    patch_code = f"    invoke-static {{{reg}}}, Lcom/android/internal/util/PropsHookUtils;->setProps(Landroid/content/Context;)V\n    invoke-static {{{reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onNewApplication(Landroid/content/Context;)V"
                    self._run_smalikit(file_path=str(inst_smali), method=method2, before_line=["return-object", patch_code])

        keystore_smali = self._find_file_recursive(work_dir, "AndroidKeyStoreSpi.smali")
        if keystore_smali:
            self._run_smalikit(file_path=str(keystore_smali), method="engineGetCertificateChain", 
                               insert_line=["2", "    invoke-static {}, Lcom/android/internal/util/danda/OemPorts10TUtils;->onEngineGetCertificateChain()V"])
            
            content = keystore_smali.read_text(encoding='utf-8')
            aput_matches = list(re.finditer(r"aput-object\s+([vp]\d+),\s+([vp]\d+),\s+([vp]\d+)", content))
            if aput_matches:
                pattern = re.compile(r"(\.method.+engineGetCertificateChain.+?\.end method)", re.DOTALL)
                match = pattern.search(content)
                if match:
                    body = match.group(1)
                    inner_aputs = list(re.finditer(r"aput-object\s+([vp]\d+),\s+([vp]\d+),\s+([vp]\d+)", body))
                    if inner_aputs:
                        last_aput = inner_aputs[-1]
                        array_reg = last_aput.group(2)
                        
                        spoof_code = f"\n    invoke-static {{{array_reg}}}, Lcom/android/internal/util/danda/OemPorts10TUtils;->genCertificateChain([Ljava/security/cert/Certificate;)[Ljava/security/cert/Certificate;\n    move-result-object {array_reg}\n"
                        
                        old_line = last_aput.group(0)
                        new_body = body.replace(old_line, old_line + spoof_code)
                        content = content.replace(body, new_body)
                        keystore_smali.write_text(content, encoding='utf-8')

        app_pm_smali = self._find_file_recursive(work_dir, "ApplicationPackageManager.smali")
        if app_pm_smali:
            self.logger.info("Hooking ApplicationPackageManager...")
            
            method_sig = "hasSystemFeature(Ljava/lang/String;I)Z"
            
            repl_pattern = (
                r"invoke-static {p1, \1}, Lcom/android/internal/util/PropsHookUtils;->hasSystemFeature(Ljava/lang/String;Z)Z"
                r"\n    move-result \1"
                r"\n    return \1"
            )
            
            self._run_smalikit(
                file_path=str(app_pm_smali), 
                method=method_sig, 
                regex_replace=(r"return\s+([vp]\d+)", repl_pattern)
            )
        
        policy_tool = self.bin_dir / "insert_selinux_policy.py"
        config_json = Path("devices/common/pif_updater_policy.json")
        cil_path = self.ctx.target_dir / "system/system/etc/selinux/plat_sepolicy.cil"
        
        if policy_tool.exists() and config_json.exists() and cil_path.exists():
            self.shell.run(["python3", str(policy_tool), "--config", str(config_json), str(cil_path)])
            
            fc_path = self.ctx.target_dir / "system/system/etc/selinux/plat_file_contexts"
            if fc_path.exists():
                with open(fc_path, "a") as f:
                    f.write("\n/system/bin/pif-updater       u:object_r:pif_updater_exec:s0\n")
                    f.write("/data/system/pif_tmp.apk  u:object_r:pif_data_file:s0\n")
                    f.write("/data/PIF.apk u:object_r:pif_data_file:s0\n")
                    f.write("/data/local/tmp/PIF.apk   u:object_r:pif_data_file:s0\n")
        
        # Properties migrated to devices/common/features.json

    # --------------------------------------------------------------------------
    # 自定义平台签名校验逻辑
    # --------------------------------------------------------------------------
    def _integrate_custom_platform_key(self, work_dir):
        epm_smali = self._find_file_recursive(work_dir, "ExtraPackageManager.smali")
        if not epm_smali: return
        self.logger.info("Injecting Custom Platform Key Check...")

        MY_PLATFORM_KEY = "308203bb308202a3a00302010202146a0b4f6a1a8f61a32d8450ead92d479dea486573300d06092a864886f70d01010b0500306c310b300906035504061302434e3110300e06035504080c075369436875616e3110300e06035504070c074368656e6744753110300e060355040a0c07504f5254524f4d31133011060355040b0c0a4d61696e7461696e65723112301006035504030c09427275636554656e673020170d3236303230323031333632385a180f32303533303632303031333632385a306c310b300906035504061302434e3110300e06035504080c075369436875616e3110300e06035504070c074368656e6744753110300e060355040a0c07504f5254524f4d31133011060355040b0c0a4d61696e7461696e65723112301006035504030c09427275636554656e6730820122300d06092a864886f70d01010105000382010f003082010a0282010100cb68bcf8927a175624a0a7428f1bbd67b4cf18c8ba42b73de9649fd2aa42935b9195b27ccd611971056654db51499ffa01783a1dbc95e03f9c557d4930193c3d04f9016a84411b502ea844fac9d463b4c9eed2d73ca3267b8a399f5da254941c7413d2a7534fd30a4ed10567933bfda249e2027ce74da667de3b6278844d232e038c2c98deb7d172a44b2fd9ec90ea74cb1c96b647044c60ce18cec93b60b84065ddd8800e10bcf465e4f3ace6d423ef2b235d75081e36b5d0f1ca858090d3dd8d74437ebb504490a8e7e9e3e2b696c3ac8e2ec856bedf4efe4e05e14f2437f81fbc8428aa330cdde0816450b4416e10f743204c17ee65b92ebc61799b4cf42b0203010001a3533051301d0603551d0e041604140a318d86cc0040341341b6dc716094da06cd4dd6301f0603551d230418301680140a318d86cc0040341341b6dc716094da06cd4dd6300f0603551d130101ff040530030101ff300d06092a864886f70d01010b0500038201010023e7aeda5403f40c794504e3edf99182a5eb53c9ddec0d93fd9fe6539e1520ea6ad08ac3215555f3fe366fa6ab01e0f45d6ce1512416c572f387a72408dde6442b76e405296cc8c128844fe68a29f6a114eb6f303e3545ea0b32d85e9c7d45cfa3c860b03d00171bb2aa4434892bf484dd390643f324a2e38a5e6ce7f26e92b3d02ac8605514b9c75a8aab9ab990c01951213f7214a36389c0759cfb68737bb3bb85dff4b1b40377279e2c82298351c276ab266869d6494b838bd6cc175185f705b8806eb1950becec57fb4f9b50240bb92d1d30bbb5764d311d18446588e5fd2b9785c635f2bb690df1e4fb595305371350c6d306d3f6cae3bc4974e9d8609c"
        
        hook_code = f"""
    # [Start] Custom Platform Key Check
    const/4 v2, 0x1
    new-array v2, v2, [Landroid/content/pm/Signature;
    new-instance v3, Landroid/content/pm/Signature;
    const-string v4, "{MY_PLATFORM_KEY}"
    invoke-direct {{v3, v4}}, Landroid/content/pm/Signature;-><init>(Ljava/lang/String;)V
    const/4 v4, 0x0
    aput-object v3, v2, v4
    invoke-static {{p0, v2}}, Lmiui/content/pm/ExtraPackageManager;->compareSignatures([Landroid/content/pm/Signature;[Landroid/content/pm/Signature;)I
    move-result v2
    if-eqz v2, :cond_custom_skip
    const/4 v2, 0x1
    return v2
    :cond_custom_skip
    # [End]"""

        self._run_smalikit(file_path=str(epm_smali), method="isTrustedPlatformSignature([Landroid/content/pm/Signature;)Z", 
                           regex_replace=(r"\.locals\s+\d+", ".locals 5"))
        
        self._run_smalikit(file_path=str(epm_smali), method="isTrustedPlatformSignature([Landroid/content/pm/Signature;)Z", 
                           insert_line=["2", hook_code])

    def _copy_to_next_classes(self, work_dir, source_dir):
        max_num = 1
        for d in work_dir.glob("smali/classes*"):
             name = d.name
             if name == "classes": num = 1
             else: 
                 try: num = int(name.replace("classes", ""))
                 except: num = 1
             if num > max_num: max_num = num
        
        target = work_dir / "smali" / f"classes{max_num + 1}"
        shutil.copytree(source_dir, target, dirs_exist_ok=True)
        self.logger.info(f"Copied classes to {target.name}")

    def _extract_register_from_invoke(self, content: str, method_signature: str, invoke_signature: str, arg_index: int = 1) -> str:
        method_pattern = re.compile(
            rf"\.method[^\n]*?{re.escape(method_signature)}(.*?)\.end method", 
            re.DOTALL
        )
        method_match = method_pattern.search(content)
        
        if not method_match:
            self.logger.warning(f"Target method not found: {method_signature}")
            return None
            
        method_body = method_match.group(1)

        invoke_pattern = re.compile(
            rf"invoke-\w+\s+{{(.*?)}},\s+{re.escape(invoke_signature)}"
        )
        invoke_match = invoke_pattern.search(method_body)
        
        if not invoke_match:
            self.logger.warning(f"Invoke signature not found in method body: {invoke_signature}")
            return None
            
        matched_regs_str = invoke_match.group(1)
        
        reg_list = [r.strip() for r in matched_regs_str.split(',') if r.strip()]
        
        if arg_index < len(reg_list):
            extracted_reg = reg_list[arg_index]
            self.logger.debug(f"Extracted register {extracted_reg} from {method_signature}")
            return extracted_reg
        else:
            self.logger.warning(f"arg_index {arg_index} out of bounds for registers: {reg_list}")
            return None

    def _inject_xeu_toolbox(self):
        xeu_zip = Path("devices/common/xeutoolbox.zip")
        if not xeu_zip.exists():
            return

        self.logger.info("Injecting Xiaomi.eu Toolbox...")

        try:
            with zipfile.ZipFile(xeu_zip, 'r') as z:
                z.extractall(self.ctx.target_dir)
            self.logger.info(f"Extracted {xeu_zip.name}")
        except Exception as e:
            self.logger.error(f"Failed to extract xeutoolbox: {e}")
            return

        target_files = [
            self.ctx.target_dir / "config/system_ext_file_contexts",
            self.ctx.target_dir / "system_ext/etc/selinux/system_ext_file_contexts"
        ]
        
        context_line = "\n/system_ext/xbin/xeu_toolbox  u:object_r:toolbox_exec:s0\n"

        for f in target_files:
            if f.exists():
                try:
                    with open(f, "a", encoding="utf-8") as file:
                        file.write(context_line)
                    self.logger.info(f"Updated contexts: {f.name}")
                except Exception as e:
                    self.logger.warning(f"Failed to append context to {f}: {e}")

        cil_file = self.ctx.target_dir / "system_ext/etc/selinux/system_ext_sepolicy.cil"
        policy_line = "\n(allow init toolbox_exec (file ((execute_no_trans))))\n"
        
        if cil_file.exists():
            try:
                with open(cil_file, "a", encoding="utf-8") as f:
                    f.write(policy_line)
                self.logger.info(f"Updated sepolicy: {cil_file.name}")
            except Exception as e:
                self.logger.warning(f"Failed to append policy to {cil_file}: {e}")
                
class FirmwareModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("FirmwareMod")
        self.shell = ShellRunner()
        self.bin_dir = Path("bin").resolve()
        
        if not self.ctx.tools.magiskboot.exists():
            self.logger.error(f"magiskboot binary not found at {self.ctx.tools.magiskboot}")
            return
        
        self.assets_dir = self.bin_dir.parent / "assets"
        self.ksu_version_file = self.assets_dir / "ksu_version.txt"
        self.repo_owner = "tiann"
        self.repo_name = "KernelSU"

    def run(self):
        self.logger.info("Starting Firmware Modification...")
        
        self._patch_vbmeta()
        
        if getattr(self.ctx, "enable_ksu", False):
            self._patch_ksu()
        
        self.logger.info("Firmware Modification Completed.")

    def _patch_vbmeta(self):
        self.logger.info("Patching vbmeta images (Disabling AVB)...")
        
        vbmeta_images = list(self.ctx.target_dir.rglob("vbmeta*.img"))
        
        if not vbmeta_images:
            self.logger.warning("No vbmeta images found in target directory.")
            return

        AVB_MAGIC = b"AVB0"
        FLAGS_OFFSET = 123
        FLAGS_TO_SET = b'\x03'

        for img_path in vbmeta_images:
            try:
                with open(img_path, "r+b") as f:
                    magic = f.read(4)
                    if magic != AVB_MAGIC:
                        self.logger.warning(f"Skipping {img_path.name}: Invalid AVB Magic")
                        continue
                    
                    f.seek(FLAGS_OFFSET)
                    f.write(FLAGS_TO_SET)
                    self.logger.info(f"Successfully patched: {img_path.name}")
                    
            except Exception as e:
                self.logger.error(f"Failed to patch {img_path.name}: {e}")

    def _patch_ksu(self):
        self.logger.info("Attempting to patch KernelSU...")
        
        target_init_boot = self.ctx.target_dir / "repack_images" / "init_boot.img"
        target_boot = self.ctx.target_dir / "repack_images" / "boot.img"
        
        if not target_init_boot.exists():
            self.logger.warning("init_boot.img not found, skipping KSU patch.")
            return
        if not target_boot.exists():
            self.logger.warning("boot.img not found (needed for KMI check), skipping KSU patch.")
            return
            
        if not self.ctx.tools.magiskboot.exists():
            self.logger.error("magiskboot binary not found!")
            return

        kmi_version = self._analyze_kmi(target_boot)
        if not kmi_version:
            self.logger.error("Failed to determine KMI version.")
            return
        
        self.logger.info(f"Detected KMI Version: {kmi_version}")

        if not self._prepare_ksu_assets(kmi_version):
            self.logger.error("Failed to prepare KSU assets.")
            return
            
        self._apply_ksu_patch(target_init_boot, kmi_version)

    def _analyze_kmi(self, boot_img):
        with tempfile.TemporaryDirectory(prefix="ksu_kmi_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(boot_img, tmp_path / "boot.img")
            
            try:
                self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path)
            except Exception:
                return None
            
            kernel_file = tmp_path / "kernel"
            if not kernel_file.exists(): return None
            
            try:
                with open(kernel_file, 'rb') as f:
                    content = f.read()
                    
                strings = []
                current = []
                for b in content:
                    if 32 <= b <= 126: current.append(chr(b))
                    else:
                        if len(current) >= 4: strings.append("".join(current))
                        current = []
                
                pattern = re.compile(r'(?:^|\s)(\d+\.\d+)\S*(android\d+)')
                for s in strings:
                    if "Linux version" in s or "android" in s:
                        match = pattern.search(s)
                        if match:
                            return f"{match.group(2)}-{match.group(1)}"
            except Exception:
                pass
        return None

    def _prepare_ksu_assets(self, kmi_version):
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        
        target_ko = self.assets_dir / f"{kmi_version}_kernelsu.ko"
        target_init = self.assets_dir / "ksuinit"
        
        if target_ko.exists() and target_init.exists():
            return True
            
        self.logger.info("Downloading KernelSU assets...")
        try:
            api_url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
            with urllib.request.urlopen(api_url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                
            assets = data.get("assets", [])
            
            for asset in assets:
                name = asset["name"]
                url = asset["browser_download_url"]
                
                if name == "ksuinit" and not target_init.exists():
                    self._download_file(url, target_init)
                elif name == f"{kmi_version}_kernelsu.ko" and not target_ko.exists():
                    self._download_file(url, target_ko)
            
            return (target_ko.exists() and target_init.exists())
            
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return False

    def _download_file(self, url, dest):
        self.logger.info(f"Downloading {dest.name}...")
        with urllib.request.urlopen(url) as remote, open(dest, 'wb') as local:
            shutil.copyfileobj(remote, local)

    def _apply_ksu_patch(self, init_boot_img, kmi_version):
        self.logger.info(f"Patching {init_boot_img.name} with KernelSU...")
        
        ko_file = self.assets_dir / f"{kmi_version}_kernelsu.ko"
        init_file = self.assets_dir / "ksuinit"
        
        with tempfile.TemporaryDirectory(prefix="ksu_patch_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(init_boot_img, tmp_path / "init_boot.img")
            
            self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "init_boot.img"], cwd=tmp_path)
            
            ramdisk = tmp_path / "ramdisk.cpio"
            if not ramdisk.exists():
                self.logger.error("ramdisk.cpio not found")
                return

            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "mv init init.real"], cwd=tmp_path)
            
            shutil.copy(init_file, tmp_path / "init")
            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "add 0755 init init"], cwd=tmp_path)
            
            shutil.copy(ko_file, tmp_path / "kernelsu.ko")
            self.shell.run([str(self.ctx.tools.magiskboot), "cpio", "ramdisk.cpio", "add 0755 kernelsu.ko kernelsu.ko"], cwd=tmp_path)

            self.shell.run([str(self.ctx.tools.magiskboot), "repack", "init_boot.img"], cwd=tmp_path)
            
            new_img = tmp_path / "new-boot.img"
            if new_img.exists():
                shutil.move(new_img, init_boot_img)
                self.logger.info("KernelSU injected successfully.")
            else:
                self.logger.error("Failed to repack init_boot.img")

class RomModifier:
    def __init__(self, context):
        self.ctx = context
        self.logger = logging.getLogger("RomModifier")
        
        self.stock_rom_img = self.ctx.stock_rom_dir
        self.target_rom_img = self.ctx.target_rom_dir

    def run_all_modifications(self):
        self.logger.info("=== Starting ROM Modification Phase ===")

        self._sync_and_patch_components()
        self._apply_overrides()
        
        self.logger.info("=== Modification Phase Completed ===")

    def _clean_bloatware(self):
        self.logger.info("Step 1: Cleaning Bloatware...")
        debloat_list = [
            "MSA", "AnalyticsCore", "MiuiDaemon", "MiuiBugReport", 
            "MiBrowserGlobal", "MiDrop", "XiaomiVip", "libbugreport.so"
        ]
        clean_rules = [{"mode": "delete", "target": item} for item in debloat_list]
        
        self.ctx.syncer.execute_rules(None, self.target_rom_img, clean_rules)

    def _sync_and_patch_components(self):
        self.logger.info("Step 2: Syncing Stock Components & Patching...")
        sync_rules = [
            {"mode": "file_to_dir", "source": "MiuiCamera.apk", "target": "MiuiCamera"},
            {"mode": "file_to_file", "source": "bootanimation.zip", "target": "bootanimation.zip"},
        ]
        
        if self.ctx.stock_rom_code == "fuxi":
            fuxi_rules = [
                {
                    "mode": "hexpatch", 
                    "target": "libmigui.so", 
                    "hex_old": "726F2E70726F647563742E70726F647563742E6E616D65",
                    "hex_new": "726F2E70726F647563742E73706F6F6665642E6E616D65"
                },
                {
                    "mode": "hexpatch", 
                    "target": "libmigui.so", 
                    "hex_old": "726F2E70726F647563742E646576696365",
                    "hex_new": "726F2E73706F6F6665642E646576696365"
                }
            ]
            # Properties migrated to devices/fuxi/features.json
            
            sync_rules.extend(fuxi_rules)       
            self._apply_wild_boost()
          
        self.ctx.syncer.execute_rules(self.stock_rom_img, self.target_rom_img, sync_rules)
     
    def _apply_overrides(self):
        self.logger.info("Step 3: Applying Physical Overrides...")
        
        # 1. Common Overrides for OS3+ (LyraSdkApp fix)
        self._apply_common_overrides()

        # 2. Device Specific Overrides
        override_dir = Path(f"devices/{self.ctx.stock_rom_code}/override/{self.ctx.port_android_version}")
        self.ctx.syncer.apply_override(override_dir, self.target_rom_img)

    def _apply_common_overrides(self):
        """
        Apply common overrides based on conditions (e.g., OS version)
        """
        # Check for OS3.0+
        # ro.mi.os.version.name usually looks like "OS1.0.5.0.UMCCNXM" or "V14.0.23..."
        # But HyperOS 2.0/3.0 might be simpler in this property or need parsing.
        # User said: ro.mi.os.version.name=OS3.0
        
        os_version_name = self.ctx.port.get_prop("ro.mi.os.version.name", "")
        self.logger.info(f"Checking for common overrides. Port OS Version: {os_version_name}")
        
        if os_version_name.startswith("OS3"):
            self.logger.info("Detected HyperOS 3.0+, applying common OS3 fixes...")
            common_os3_dir = Path("devices/common/override/os3")
            if common_os3_dir.exists():
                self.ctx.syncer.apply_override(common_os3_dir, self.target_rom_img)
            else:
                self.logger.warning(f"Common OS3 override directory not found at {common_os3_dir}")

    def _apply_wild_boost(self):
        self.logger.info("Applying Kernel 5.15 perfmgr (Wild Boost)...")
        import zipfile
        
        wild_boost_zip = Path("devices/common/wild_boost_5.15.zip")
        
        if not wild_boost_zip.exists():
            self.logger.warning(f"Wild Boost package not found at {wild_boost_zip}. Skipping.")
            return

        try:
            with zipfile.ZipFile(wild_boost_zip, 'r') as zip_ref:
                zip_ref.extractall(self.target_rom_img)
            self.logger.debug(f"     [+] Extracted {wild_boost_zip.name} to {self.target_rom_img}")
                
            modules_dir = self.target_rom_img / "vendor_dlkm/lib/modules"
            
            if modules_dir.exists():
                load_file = modules_dir / "modules.load"
                with open(load_file, "a") as f:
                    f.write("perfmgr.ko\n")
                self.logger.debug(f"     [+] Appended perfmgr.ko to {load_file.relative_to(self.target_rom_img)}")

                dep_file = modules_dir / "modules.dep"
                with open(dep_file, "a") as f:
                    f.write("/vendor/lib/modules/perfmgr.ko:\n")
                self.logger.debug(f"     [+] Appended perfmgr.ko to {dep_file.relative_to(self.target_rom_img)}")
            else:
                self.logger.warning(f"     [!] Directory {modules_dir} not found. Cannot append module dependencies.")

        except Exception as e:
            self.logger.error(f"     [X] Failed to apply Wild Boost: {e}")
