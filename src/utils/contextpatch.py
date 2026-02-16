import os
from re import sub
from difflib import SequenceMatcher
import logging
from pathlib import Path

from typing import Generator

class ContextPatcher:
    def __init__(self):
        self.logger = logging.getLogger("ContextPatcher")
        # Define fixed permissions for specific paths
        self.fix_permission = {
            "/vendor/bin/hw/android.hardware.wifi@1.0": ["u:object_r:hal_wifi_default_exec:s0"],
            "/system/system/bin/pif-updater": ["u:object_r:pif_updater_exec:s0"],
            "/vendor/app/PIF.apk": ["u:object_r:vendor_app_file:s0"],
            # Add more fixed permissions here if needed
        }

    def scan_context(self, file) -> dict:  
        """Read context file and return a dictionary"""
        context = {}
        try:
            with open(file, "r", encoding='utf-8') as file_:
                for line in file_:
                    # Filter empty lines and comments
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = line.replace('\\', '').split()
                    if not parts: 
                        continue
                        
                    filepath, *other = parts
                    context[filepath] = other
        except Exception as e:
            self.logger.error(f"Error scanning context file {file}: {e}")
        return context

    def scan_dir(self, folder) -> Generator[str, None, None]:  
        """Scan directory and yield paths formatted for context file"""
        folder_str = str(folder)
        part_name = os.path.basename(folder_str)
        
        # Hardcoded paths that might not be in the filesystem traversal
        allfiles = ['/', '/lost+found', f'/{part_name}/lost+found', f'/{part_name}', f'/{part_name}/']
        
        for root, dirs, files in os.walk(folder_str, topdown=True):
            for dir_ in dirs:
                # Format: /part_name/path/to/dir
                yield os.path.join(root, dir_).replace(folder_str, '/' + part_name).replace('\\', '/')
            for file in files:
                # Format: /part_name/path/to/file
                yield os.path.join(root, file).replace(folder_str, '/' + part_name).replace('\\', '/')
        
        for rv in allfiles:
            yield rv

    def context_patch(self, fs_file, dir_path) -> tuple:  
        """
        Compare filesystem against context file and patch missing entries.
        Returns: (new_fs_dict, added_count)
        """
        new_fs = {}
        # r_new_fs tracks newly added entries to prevent duplicates and for debugging
        r_new_fs = {} 
        add_new = 0
        permission_d = None
        dir_path_str = str(dir_path)
        
        self.logger.info(f"Loaded {len(fs_file)} entries from origin context.")
        
        # Determine default permission based on partition
        try:
            if dir_path_str.endswith('/system'):
                permission_d = ['u:object_r:system_file:s0']
            elif dir_path_str.endswith('/vendor'):
                permission_d = ['u:object_r:vendor_file:s0']
            else:
                # Fallback: try to pick an arbitrary permission from existing context
                if len(fs_file) > 5:
                    permission_d = fs_file.get(list(fs_file)[5])
        except Exception:
            pass
            
        if not permission_d:
            permission_d = ['u:object_r:system_file:s0']
            
        self.logger.debug(f"Default permission set to: {permission_d}")

        # Iterate through all files in the directory
        for i in self.scan_dir(os.path.abspath(dir_path_str)):
            # If entry exists in original context, keep it
            if fs_file.get(i):
                # Escape special characters for regex-like format in file_contexts
                safe_path = sub(r'([^-_/a-zA-Z0-9])', r'\\\1', i)
                new_fs[safe_path] = fs_file[i]
            else:
                # Entry missing, need to add it
                if r_new_fs.get(i):
                    continue # Already added
                
                permission = permission_d
                
                if i:
                    # 1. Check fixed permissions
                    if i in self.fix_permission:
                        permission = self.fix_permission[i]
                    else:
                        # 2. Fuzzy match: Find closest parent directory in existing context
                        parent_path = os.path.dirname(i)
                        
                        matched = False
                        # Optimization: Use keys iterator directly
                        for e in fs_file.keys():
                            # quick_ratio is faster for high volume comparisons
                            if SequenceMatcher(None, parent_path, e).quick_ratio() >= 0.85:
                                if e == parent_path: 
                                    continue
                                permission = fs_file[e]
                                matched = True
                                break
                        
                        if not matched:
                            permission = permission_d

                
                if i:
                    # 1. Check fixed permissions
                    if i in self.fix_permission:
                        permission = self.fix_permission[i]
                    else:
                        # 2. Fuzzy match: Find closest parent directory in existing context
                        parent_path = os.path.dirname(i)
                        
                        matched = False
                        # Optimization: Use keys iterator directly
                        for e in fs_file.keys():
                            # quick_ratio is faster for high volume comparisons
                            if SequenceMatcher(None, parent_path, e).quick_ratio() >= 0.85:
                                if e == parent_path: 
                                    continue
                                permission = fs_file[e]
                                matched = True
                                break
                        
                        if not matched:
                            permission = permission_d

                add_new += 1
                r_new_fs[i] = permission
                
                safe_path = sub(r'([^-_/a-zA-Z0-9])', r'\\\1', i)
                new_fs[safe_path] = permission
                
                # [DEBUG] Log the added entry
                self.logger.debug(f"[NEW ENTRY] {i} -> {permission}")

        return new_fs, add_new

    def patch(self, dir_path: Path, fs_config: Path) -> None:
        """Main entry point to patch a partition's file_contexts"""
        dir_path_str = str(dir_path)
        fs_config_str = str(fs_config)
        
        if not os.path.exists(dir_path_str) or not os.path.exists(fs_config_str):
            self.logger.warning(f"Path or config not found: {dir_path_str} | {fs_config_str}")
            return
            
        self.logger.info(f"Patching contexts for {os.path.basename(dir_path_str)}...")
        
        fs_file = self.scan_context(os.path.abspath(fs_config_str))
        new_fs, add_new = self.context_patch(fs_file, dir_path_str)
        
        # Write back to file
        try:
            with open(fs_config_str, "w", encoding='utf-8', newline='\n') as f:
                # Sort by path for consistency
                for path in sorted(new_fs.keys()):
                    line = f"{path} {' '.join(new_fs[path])}\n"
                    f.write(line)
                    
            self.logger.info(f"Context patch done. Added {add_new} new entries to {os.path.basename(fs_config_str)}.")
        except Exception as e:
            self.logger.error(f"Failed to write context file: {e}")
