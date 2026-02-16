import shutil
import time
import subprocess
import logging
from pathlib import Path

class ROMSyncEngine:
    def __init__(self,context, logger: logging.Logger):
        self.ctx = context
        self.logger = logger
        self._stock_rom_cache = {}
        self._target_rom_cache = {}
        self._target_package_cache = {}

    def _build_cache(self, directory: Path) -> dict:
        """Scan directory and build global index dictionary (O(1) lookup)"""
        cache = {}
        if not directory or not directory.exists():
            return cache
            
        self.logger.info(f"Building index cache for {directory.name}...")
        start_time = time.time()
        
        for path in directory.rglob("*"):
            name_lower = path.name.lower()
            if name_lower not in cache:
                cache[name_lower] = []
            cache[name_lower].append(path)
                
        elapsed = time.time() - start_time
        item_count = sum(len(v) for v in cache.values())
        self.logger.info(f"Cache built in {elapsed:.2f}s. Indexed {item_count} items.")
        return cache
        
    def _get_matches(self, cache: dict, name: str) -> list:
        """
        Supports both pure filenames and precise relative paths.
        e.g. "build.prop" -> returns all build.prop
        e.g. "product/etc/build.prop" -> returns only the one matching the suffix
        """
        if not name:
            return []
            
        name_obj = Path(name)
        base_name = name_obj.name.lower()
        
        # 1. O(1) Get all candidates with same name
        candidates = cache.get(base_name, [])
        
        # 2. If user provided precise path with directory (multi-level)
        if len(name_obj.parts) > 1:
            filtered = []
            for p in candidates:
                # Match last N parts
                if p.parts[-len(name_obj.parts):] == name_obj.parts:
                    filtered.append(p)
            return filtered
            
        return candidates

    def execute_rules(self, source_dir: Path, target_dir: Path, rules: list):
        """
        Execute all ROM porting and trimming rules
        Supported modes: file_to_dir, file_to_file, dir_to_dir, hexpatch, prop_append, delete
        """
        # Lazy load global cache
        if source_dir and not self._stock_rom_cache:
            self._stock_rom_cache = self._build_cache(source_dir)
        if target_dir and not self._target_rom_cache:
            self._target_rom_cache = self._build_cache(target_dir)

        self.logger.info(f"Executing {len(rules)} porting rules...")

        for rule in rules:
            mode = rule.get("mode")
            src_name = rule.get("source")
            tgt_name = rule.get("target")

            self.logger.info(f"  -> [{mode.upper()}] Processing {tgt_name} ...")

            # 1. Process modes requiring source file copy
            if mode in ["file_to_dir", "file_to_file", "dir_to_dir"]:
                src_matches = self._get_matches(self._stock_rom_cache, src_name)
                tgt_matches = self._get_matches(self._target_rom_cache, tgt_name)

                if not src_matches:
                    self.logger.warning(f"     [!] Source '{src_name}' not found. Skipped.")
                    continue
                if not tgt_matches:
                    self.logger.warning(f"     [!] Target '{tgt_name}' not found. Skipped.")
                    continue

                src_match, tgt_match = src_matches[0], tgt_matches[0]

                try:
                    if mode == "file_to_dir":
                        if tgt_match.is_dir():
                            shutil.copy2(src_match, tgt_match)
                            self.logger.debug(f"     [+] Copied to {tgt_match.relative_to(target_dir)}")
                    elif mode == "file_to_file":
                        shutil.copy2(src_match, tgt_match)
                        self.logger.debug(f"     [+] Replaced {tgt_match.relative_to(target_dir)}")
                    elif mode == "dir_to_dir":
                        if tgt_match.exists():
                            shutil.rmtree(tgt_match)
                        shutil.copytree(src_match, tgt_match)
                        self.logger.debug(f"     [+] Replaced dir {tgt_match.relative_to(target_dir)}")
                except Exception as e:
                    self.logger.error(f"     [X] Error syncing {src_name}: {e}")

            # 2. Process modes modifying target file (HexPatch)
            elif mode == "hexpatch":
                tgt_matches = self._get_matches(self._target_rom_cache, tgt_name)
                if not tgt_matches:
                    self.logger.warning(f"     [!] Target '{tgt_name}' not found for hexpatch.")
                    continue
                
                for tgt_match in tgt_matches:
                    try:
                        cmd = [str(self.ctx.tools.magiskboot), "--hexpatch", str(tgt_match), rule["hex_old"], rule["hex_new"]]
                        subprocess.run(cmd, check=True, capture_output=True)
                        self.logger.debug(f"     [+] HexPatched {tgt_match.relative_to(target_dir)}")
                    except subprocess.CalledProcessError as e:
                        self.logger.error(f"     [X] Magiskboot failed on {tgt_match.name}: {e.stderr.decode('utf-8', errors='ignore')}")

            # 3. Process property append mode (Build.prop)
            elif mode == "prop_append":
                tgt_matches = self._get_matches(self._target_rom_cache, tgt_name)
                if not tgt_matches:
                    self.logger.warning(f"     [!] Target '{tgt_name}' not found for prop append.")
                    continue
                
                tgt_match = tgt_matches[0]
                try:
                    with open(tgt_match, "a", encoding="utf-8") as f:
                        f.write("\n" + "\n".join(rule["lines"]) + "\n")
                    self.logger.debug(f"     [+] Appended props to {tgt_match.relative_to(target_dir)}")
                except Exception as e:
                    self.logger.error(f"     [X] Error writing props: {e}")

            # 4. Process delete mode (Trim bloatware)
            elif mode == "delete":
                tgt_matches = self._get_matches(self._target_rom_cache, tgt_name)
                if not tgt_matches:
                    self.logger.debug(f"     [!] Target '{tgt_name}' already absent or not found. Skipped.")
                    continue
                
                for tgt_match in tgt_matches:
                    try:
                        if tgt_match.is_dir():
                            shutil.rmtree(tgt_match)
                            self.logger.debug(f"     [-] Removed directory {tgt_match.relative_to(target_dir)}")
                        else:
                            tgt_match.unlink()
                            self.logger.debug(f"     [-] Removed file {tgt_match.relative_to(target_dir)}")
                    except Exception as e:
                        self.logger.error(f"     [X] Error deleting {tgt_match.name}: {e}")
            else:
                self.logger.error(f"     [X] Unknown mode '{mode}'")


    def apply_override(self, override_dir: Path, target_dir: Path):
        """
        Intelligent physical override mechanism
        1. Find and remove old files and their mount directories in target (solve Pangu framework residue).
        2. Copy override files strictly by relative path into target.
        """
        if not override_dir.exists():
            self.logger.info(f"Override directory '{override_dir}' not found. Skipping override phase.")
            return

        self.logger.info(f"Applying intelligent overrides from {override_dir}...")
        
        if not self._target_rom_cache:
            self._target_rom_cache = self._build_cache(target_dir)
        if not self._target_package_cache:
            self._build_package_cache(target_dir)
            
        override_count = 0
        
        for override_file in override_dir.rglob("*"):
            if not override_file.is_file():
                continue

            file_name_lower = override_file.name.lower()
            
            # 1. Residual file cleanup logic
            if override_file.suffix.lower() == ".apk":
                override_pkg_name = self._get_apk_package_name(override_file)
                tgt_matches = []
                
                if override_pkg_name:
                    # If package name parsed successfully, search cache for old APK location
                    tgt_matches = self._target_package_cache.get(override_pkg_name, [])
                    if tgt_matches:
                        self.logger.debug(f"     [!] Found target by Package Name: {override_pkg_name}")
                
                # [Fallback] If aapt2 fails or pkg name not found, fallback to filename search
                if not tgt_matches:
                    tgt_matches = self._target_rom_cache.get(file_name_lower, [])
                    
                if tgt_matches:
                    for old_file in tgt_matches:
                        old_dir = old_file.parent
                        
                        protected_dirs = {
                            "app", "priv-app", "system", "product", "system_ext", "vendor",
                            "overlay", "framework", "mi_ext", "odm", "oem",
                            "bin", "lib", "lib64", "etc", "media", "fonts"
                        }
                        # Only delete specific independent App folder, prevent accidental deletion of root dirs like system/app
                        if old_dir.name not in protected_dirs:
                            self.logger.debug(f"     [-] Erasing old APK directory: {old_dir.relative_to(target_dir)}")
                            try:
                                shutil.rmtree(old_dir)
                            except Exception as e:
                                self.logger.error(f"     [X] Failed to erase old directory {old_dir.name}: {e}")
                        else:
                            if old_file.exists():
                                old_file.unlink()
                else:
                    self.logger.debug(f"     [!] New APK '{override_file.name}' not found in target. Will inject as new.")
            else:
                tgt_matches = self._target_rom_cache.get(file_name_lower, [])
                if tgt_matches:
                    for old_file in tgt_matches:
                        if old_file.exists():
                            old_file.unlink()

            # 2. Precise mapping copy
            relative_path = override_file.relative_to(override_dir)
            final_target_path = target_dir / relative_path
            
            final_target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(override_file, final_target_path)
            
            self.logger.info(f"     [+] Overrode: {relative_path}")
            override_count += 1

        self.logger.info(f"Successfully applied {override_count} overrides.")
        
    def _get_apk_package_name(self, apk_path: Path) -> str | None:
        """
        Use aapt2 to parse APK package name (extremely fast)
        """
        if not apk_path.exists() or not self.ctx.tools.aapt2:
            return None
            
        cmd = [str(self.ctx.tools.aapt2), "dump", "packagename", str(apk_path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            # aapt2 output is just the package name text, e.g.: com.android.settings
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"Failed to parse package name for {apk_path.name}: {e.stderr.strip()}")
            return None

    def _build_package_cache(self, directory: Path):
        """Scan all APKs in directory, extract and cache their Package Name"""
        if not directory or not directory.exists():
            return
            
        self.logger.info(f"Building APK package name cache for {directory.name}...")
        start_time = time.time()
        
        apk_count = 0
        # Only scan .apk files, skip .odex, .vdex etc.
        for apk_path in directory.rglob("*.apk"):
            pkg_name = self._get_apk_package_name(apk_path)
            if pkg_name:
                if pkg_name not in self._target_package_cache:
                    self._target_package_cache[pkg_name] = []
                self._target_package_cache[pkg_name].append(apk_path)
                apk_count += 1
                
        elapsed = time.time() - start_time
        self.logger.info(f"Package cache built in {elapsed:.2f}s. Indexed {apk_count} APKs.")
