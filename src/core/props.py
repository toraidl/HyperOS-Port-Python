import os
import time
import re
import logging
from pathlib import Path
from datetime import datetime, timezone

class PropertyModifier:
    def __init__(self, context):
        """
        :param context: PortingContext object
        """
        self.ctx = context
        self.logger = logging.getLogger("PropModifier")
        
        # Custom build info (can be passed from external parameters)
        self.build_user = os.getenv("BUILD_USER", "Bruce")
        self.build_host = os.getenv("BUILD_HOST", "HyperOS-Port")

    def run(self):
        """Execute all property modification logic"""
        self.logger.info("Starting build.prop modifications...")
        
        # 1. Global replacement (time, code, fingerprint, etc.)
        self._update_general_info()
        
        # 2. Screen density (DPI) migration
        self._update_density()
        
        # 3. Apply specific fixes (Millet, Blur, Cgroup)
        self._apply_specific_fixes()
        
        self._regenerate_fingerprint()
        
        self._optimize_core_affinity()
        
        self.logger.info("Build.prop modifications completed.")

    def _update_general_info(self):
        """Corresponds to most logic in Shell script 'modifying build.prop'"""
        
        # Generate timestamp
        now = datetime.now(timezone.utc)
        build_date = now.strftime("%a %b %d %H:%M:%S UTC %Y")
        build_utc = str(int(now.timestamp()))
        
        base_code = self.ctx.stock_rom_code
        rom_version = self.ctx.target_rom_version
        
        self.logger.debug(f"General Info Update: BaseCode={base_code}, ROMVersion={rom_version}")
        
        # Key-value mapping to replace
        replacements = {
            "ro.build.date=": f"ro.build.date={build_date}",
            "ro.build.date.utc=": f"ro.build.date.utc={build_utc}",
            "ro.odm.build.date=": f"ro.odm.build.date={build_date}",
            "ro.odm.build.date.utc=": f"ro.odm.build.date.utc={build_utc}",
            "ro.vendor.build.date=": f"ro.vendor.build.date={build_date}",
            "ro.vendor.build.date.utc=": f"ro.vendor.build.date.utc={build_utc}",
            "ro.system.build.date=": f"ro.system.build.date={build_date}",
            "ro.system.build.date.utc=": f"ro.system.build.date.utc={build_utc}",
            "ro.product.build.date=": f"ro.product.build.date={build_date}",
            "ro.product.build.date.utc=": f"ro.product.build.date.utc={build_utc}",
            "ro.system_ext.build.date=": f"ro.system_ext.build.date={build_date}",
            "ro.system_ext.build.date.utc=": f"ro.system_ext.build.date.utc={build_utc}",
            
            # Device code replacement
            "ro.product.device=": f"ro.product.device={base_code}",
            "ro.product.product.name=": f"ro.product.product.name={base_code}",
            "ro.product.odm.device=": f"ro.product.odm.device={base_code}",
            "ro.product.vendor.device=": f"ro.product.vendor.device={base_code}",
            "ro.product.system.device=": f"ro.product.system.device={base_code}",
            "ro.product.board=": f"ro.product.board={base_code}",
            "ro.product.system_ext.device=": f"ro.product.system_ext.device={base_code}",
            "ro.mi.os.version.incremental=" : f"ro.mi.os.version.incremental={rom_version}",
            "ro.build.version.incremental=" : f"ro.build.version.incremental={rom_version}",
            "ro.product.build.version.incremental=" : f"ro.product.build.version.incremental={rom_version}",
            
            # Other misc
            "persist.sys.timezone=": "persist.sys.timezone=Asia/Shanghai",
            "ro.build.user=": f"ro.build.user={self.build_user}",
        }

        # EU version check
        is_eu = getattr(self.ctx, "is_port_eu_rom", False)
        if is_eu:
            replacements["ro.product.mod_device="] = f"ro.product.mod_device={base_code}_xiaomieu_global"
            replacements["ro.build.host="] = "ro.build.host=xiaomi.eu"
        else:
            replacements["ro.product.mod_device="] = f"ro.product.mod_device={base_code}"
            replacements["ro.build.host="] = f"ro.build.host={self.build_host}"

        # Iterate all build.prop and modify
        for prop_file in self.ctx.target_dir.rglob("build.prop"):
            lines = []
            with open(prop_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            new_lines = []
            file_changed = False
            for line in lines:
                original_line = line
                line = line.strip()
                
                # 1. Dictionary replacement logic
                replaced = False
                for prefix, new_val in replacements.items():
                    if line.startswith(prefix):
                        if original_line.strip() != new_val:
                            self.logger.debug(f"[{prop_file.name}] Replace: {line} -> {new_val}")
                            new_lines.append(new_val + "\n")
                            file_changed = True
                        else:
                             new_lines.append(original_line)
                        replaced = True
                        break
                if replaced: continue

                # 2. Delete logic
                if line.startswith("ro.miui.density.primaryscale="):
                    self.logger.debug(f"[{prop_file.name}] Remove: {line}")
                    file_changed = True
                    continue

                new_lines.append(original_line)
            
            # Write back file
            if file_changed:
                self.logger.debug(f"Writing changes to {prop_file.relative_to(self.ctx.target_dir)}")
                with open(prop_file, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)

    def _update_density(self):
        """Screen density modification"""
        self.logger.info("Updating screen density...")
        
        # 1. Get density from base
        base_density = None
        for part in ["product", "system"]:
            val = self.ctx.stock.get_prop("ro.sf.lcd_density")
            if val:
                base_density = val
                break
        
        if not base_density:
            base_density = "440"
            self.logger.warning(f"Base density not found, defaulting to {base_density}")
        else:
            self.logger.info(f"Found Base density: {base_density}")

        # 2. Modify porting package
        found_in_port = False
        target_props = list(self.ctx.target_dir.rglob("build.prop"))
        
        for prop_file in target_props:
            content = prop_file.read_text(encoding='utf-8', errors='ignore')
            new_content = content
            
            # Replace ro.sf.lcd_density
            if "ro.sf.lcd_density=" in content:
                self.logger.debug(f"[{prop_file.name}] Updating ro.sf.lcd_density to {base_density}")
                new_content = re.sub(r"ro\.sf\.lcd_density=.*", f"ro.sf.lcd_density={base_density}", new_content)
                found_in_port = True
            
            # Replace persist.miui.density_v2
            if "persist.miui.density_v2=" in content:
                 self.logger.debug(f"[{prop_file.name}] Updating persist.miui.density_v2 to {base_density}")
                 new_content = re.sub(r"persist\.miui\.density_v2=.*", f"persist.miui.density_v2={base_density}", new_content)
            
            if content != new_content:
                prop_file.write_text(new_content, encoding='utf-8')

        # 3. If not found, append to product/etc/build.prop
        if not found_in_port:
            product_prop = self.ctx.target_dir / "product/etc/build.prop"
            if product_prop.exists():
                with open(product_prop, "a", encoding='utf-8') as f:
                    f.write(f"\nro.sf.lcd_density={base_density}\n")
                    self.logger.info(f"Appended ro.sf.lcd_density={base_density} to {product_prop.relative_to(self.ctx.target_dir)}")
            else:
                self.logger.warning(f"Could not find product/etc/build.prop to append density.")

    def _apply_specific_fixes(self):
        """Device-specific fixes (Millet, Blur, Cgroup, etc.)"""
        self.logger.info("Applying device-specific fixes...")

        # --- 1. cust_erofs ---
        product_prop = self.ctx.target_dir / "product/etc/build.prop"
        if product_prop.exists():
            self._update_or_append_prop(product_prop, "ro.miui.cust_erofs", "0")

        # --- 2. Millet Fix ---
        millet_ver = self.ctx.stock.get_prop("ro.millet.netlink")
        if not millet_ver:
            self.logger.warning("ro.millet.netlink not found in base, defaulting to 29")
            millet_ver = "29"
        else:
            self.logger.debug(f"Found base millet version: {millet_ver}")
        
        self._update_or_append_prop(product_prop, "ro.millet.netlink", millet_ver)

        # --- 3. Blur Fix ---
        self._update_or_append_prop(product_prop, "persist.sys.background_blur_supported", "true")
        self._update_or_append_prop(product_prop, "persist.sys.background_blur_version", "2")

        # --- 4. Vendor Fixes (Cgroup) ---
        vendor_prop = self.ctx.target_dir / "vendor/build.prop"
        if vendor_prop.exists():
            content = vendor_prop.read_text(encoding='utf-8', errors='ignore')
            if "persist.sys.millet.cgroup1" in content and "#persist" not in content:
                self.logger.debug(f"[{vendor_prop.name}] Commenting out persist.sys.millet.cgroup1")
                content = content.replace("persist.sys.millet.cgroup1", "#persist.sys.millet.cgroup1")
                vendor_prop.write_text(content, encoding='utf-8')

    def _update_or_append_prop(self, file_path: Path, key: str, value: str):
        """Helper function: update or append property"""
        if not file_path.exists(): return
        
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        pattern = f"{re.escape(key)}=.*"
        replacement = f"{key}={value}"
        
        match = re.search(pattern, content)
        if match:
            if match.group(0) != replacement:
                self.logger.debug(f"[{file_path.name}] Update: {key} -> {value}")
                new_content = re.sub(pattern, replacement, content)
                file_path.write_text(new_content, encoding='utf-8')
        else:
            self.logger.debug(f"[{file_path.name}] Append: {key}={value}")
            new_content = content + f"\n{replacement}\n"
            file_path.write_text(new_content, encoding='utf-8')
    
    def _regenerate_fingerprint(self):
        """
        Regenerate ro.build.fingerprint and ro.build.description based on modified properties
        Format: Brand/Name/Device:Release/ID/Incremental:Type/Tags
        """
        self.logger.info("Regenerating build fingerprint...")

        def get_current_prop(key, default=""):
            # Priority: product -> system -> vendor
            for part in ["product", "system", "vendor","mi_ext"]:
                for prop_file in (self.ctx.target_dir / part).rglob("build.prop"):
                    try:
                        with open(prop_file, 'r', errors='ignore') as f:
                            for line in f:
                                if line.strip().startswith(f"{key}="):
                                    return line.split("=", 1)[1].strip()
                    except: pass
            return default

        # Read components
        brand = get_current_prop("ro.product.brand", "Xiaomi")
        name = get_current_prop("ro.product.mod_device")
        device = get_current_prop("ro.product.device", "miproduct")
        version = get_current_prop("ro.build.version.release")
        build_id = get_current_prop("ro.build.id")
        incremental = get_current_prop("ro.build.version.incremental")
        build_type = get_current_prop("ro.build.type", "user")
        tags = get_current_prop("ro.build.tags", "release-keys")

        self.logger.debug(f"Fingerprint components: Brand={brand}, Name={name}, Device={device}, Ver={version}, ID={build_id}, Inc={incremental}")

        # Construct Fingerprint
        new_fingerprint = f"{brand}/{name}/{device}:{version}/{build_id}/{incremental}:{build_type}/{tags}"
        self.logger.info(f"New Fingerprint: {new_fingerprint}")

        # Construct Description
        new_description = f"{name}-{build_type} {version} {build_id} {incremental} {tags}"
        self.logger.debug(f"New Description: {new_description}")

        # Write to all build.prop files
        replacements = {
            "ro.build.fingerprint=": f"ro.build.fingerprint={new_fingerprint}",
            "ro.bootimage.build.fingerprint=": f"ro.bootimage.build.fingerprint={new_fingerprint}",
            "ro.system.build.fingerprint=": f"ro.system.build.fingerprint={new_fingerprint}",
            "ro.product.build.fingerprint=": f"ro.product.build.fingerprint={new_fingerprint}",
            "ro.system_ext.build.fingerprint=": f"ro.system_ext.build.fingerprint={new_fingerprint}",
            "ro.vendor.build.fingerprint=": f"ro.vendor.build.fingerprint={new_fingerprint}",
            "ro.odm.build.fingerprint=": f"ro.odm.build.fingerprint={new_fingerprint}",
            
            "ro.build.description=": f"ro.build.description={new_description}",
            "ro.system.build.description=": f"ro.system.build.description={new_description}"
        }

        for prop_file in self.ctx.target_dir.rglob("build.prop"):
            lines = []
            try:
                with open(prop_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except: continue

            new_lines = []
            file_changed = False
            for line in lines:
                original = line
                line = line.strip()
                replaced = False
                for prefix, new_val in replacements.items():
                    if line.startswith(prefix):
                        if original.strip() != new_val:
                            new_lines.append(new_val + "\n")
                            file_changed = True
                        else:
                            new_lines.append(original)
                        replaced = True
                        break
                if not replaced:
                    new_lines.append(original)
            
            if file_changed:
                self.logger.debug(f"Updated fingerprint in {prop_file.relative_to(self.ctx.target_dir)}")
                with open(prop_file, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)    

    def _optimize_core_affinity(self):
        """
        Core allocation and scheduler optimization (supports sm8250, sm8450, sm8550 and Android version differences)
        """
        self.logger.info("Optimizing core affinity and scheduler...")
        
        product_prop = self.ctx.target_dir / "product/etc/build.prop"
        if not product_prop.exists():
            self.logger.warning("product/etc/build.prop not found, skipping core affinity optimization.")
            return

        # 1. Helper function: detect platform code
        def get_platform_code():
            vendor_prop = self.ctx.target_dir / "vendor/build.prop"
            if vendor_prop.exists():
                try:
                    content = vendor_prop.read_text(encoding='utf-8', errors='ignore')
                    if "sm8550" in content: return "sm8550"
                    if "sm8450" in content: return "sm8450"
                    if "sm8250" in content: return "sm8250"
                except: pass
            return "unknown"

        # 2. Define property config dictionaries
        
        # === SM8550 (Snapdragon 8 Gen 2) ===
        props_sm8550 = {
            "persist.sys.miui_animator_sched.bigcores": "3-6",
            "persist.sys.miui_animator_sched.sched_threads": "2",
            "persist.sys.miui_animator_sched.big_prime_cores": "3-7",
            "persist.vendor.display.miui.composer_boost": "4-7",
            "persist.sys.brightmillet.enable": "true",
            "persist.sys.millet.newversion": "true",
            "ro.miui.affinity.sfre": "2-6",
            "ro.miui.affinity.sfui": "2-6",
            "ro.miui.affinity.sfuireset": "0-6",
            "persist.sys.millet.handshake": "true"
        }

        # === SM8450 (Snapdragon 8 Gen 1) ===
        props_sm8450 = {
            "persist.sys.miui_animator_sched.bigcores": "4-7",
            "persist.sys.miui_animator_sched.big_prime_cores": "4-7",
            "persist.vendor.display.miui.composer_boost": "4-7",
            "ro.miui.affinity.sfui": "4-7",
            "ro.miui.affinity.sfre": "4-7",
        }

        # === SM8250 (Snapdragon 865) ===
        props_sm8250 = {
            "persist.sys.miui_animator_sched.bigcores": "4-7",
            "persist.sys.miui_animator_sched.big_prime_cores": "4-7",
            "ro.miui.affinity.sfui": "4-7",
        }

        # === Android 15 (Generic) ===
        props_a15_generic = {
            "ro.miui.affinity.sfui": "4-7",
            "ro.miui.affinity.sfre": "4-7",
            "ro.miui.affinity.sfuireset": "4-7",
            "persist.sys.miui_animator_sched.bigcores": "4-7",
            "persist.sys.miui_animator_sched.big_prime_cores": "4-7",
            "persist.vendor.display.miui.composer_boost": "4-7",
        }

        # === Default / Android 14 (Generic) ===
        props_default = {
            "persist.sys.miui_animator_sched.bigcores": "4-6",
            "persist.sys.miui_animator_sched.big_prime_cores": "4-7",
            "persist.sys.miui.sf_cores": "4-7",
            "persist.sys.minfree_def": "73728,92160,110592,154832,482560,579072",
            "persist.sys.minfree_6g": "73728,92160,110592,258048,663552,903168",
            "persist.sys.minfree_8g": "73728,92160,110592,387072,1105920,1451520",
            "persist.vendor.display.miui.composer_boost": "4-7",
        }

        # 3. Get state
        platform = get_platform_code()
        android_ver = str(self.ctx.port_android_version)
        
        self.logger.info(f"Applying scheduling for Platform: [{platform}], Android: [{android_ver}]")

        # 4. Match logic
        target_props = {}
        
        match (platform, android_ver):
            case ("sm8550", _):
                target_props = props_sm8550
            
            case ("sm8450", _):
                target_props = props_sm8450
                
            case ("sm8250", _):
                target_props = props_sm8250

            case ("unknown", "15"):
                target_props = props_a15_generic
            
            case _:
                target_props = props_default

        # 5. Batch apply
        if target_props:
            self.logger.debug(f"Applying {len(target_props)} scheduling properties...")
            for key, value in target_props.items():
                self._update_or_append_prop(product_prop, key, value)
