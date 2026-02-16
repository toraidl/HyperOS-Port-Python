import os
import logging
from pathlib import Path

logger = logging.getLogger("FsPatcher")

def load_fs_config(file_path: Path) -> dict:
    """Read fs_config file into a dictionary"""
    config = {}
    if not file_path.exists():
        return config
        
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            parts = line.split()
            if len(parts) >= 2:
                filepath = parts[0]
                attrs = parts[1:]
                config[filepath] = attrs
    return config

def scan_dir_recursive(folder: Path, prefix: str = "") -> list:
    """
    Recursively scan directory
    :param prefix: Path prefix to add (e.g., /mi_ext)
    """
    folder = Path(folder).resolve()
    paths = []
    
    # Root directory itself (with prefix)
    # If prefix is "/mi_ext", root is "/mi_ext"
    # If prefix is "", root is "/"
    root_item = prefix if prefix else "/"
    paths.append(root_item)

    for root, dirs, files in os.walk(folder):
        root_path = Path(root)
        
        # Iterate over all items (directories + files)
        for name in dirs + files:
            full_path = root_path / name
            
            # Calculate relative path, e.g., "etc/init/init.rc"
            rel_path_raw = str(full_path.relative_to(folder)).replace("\\", "/")
            
            # Append prefix: "/mi_ext" + "/" + "etc/init/init.rc"
            if prefix:
                final_path = f"{prefix}/{rel_path_raw}".replace("//", "/")
            else:
                final_path = f"/{rel_path_raw}"
            
            paths.append(final_path)
            
    return paths

def get_file_mode(rel_path: str, is_dir: bool, is_link: bool) -> tuple:
    """Guess uid, gid, mode based on path characteristics"""
    # Default values
    uid = '0'
    gid = '0'
    mode = '0644'
    
    # Special groups
    if any(x in rel_path for x in ["/system/bin", "/system/xbin", "/vendor/bin"]):
        gid = '2000' # shell group

    if is_dir:
        mode = '0755'
    elif is_link:
        if any(x in rel_path for x in ["/bin", "/xbin"]):
             mode = '0755'
        elif ".sh" in rel_path:
             mode = "0750"
        else:
             mode = "0644" # Permissions for links usually don't matter
    else:
        # Regular file
        if any(x in rel_path for x in ["/bin", "/xbin"]):
             mode = '0755'
        elif ".sh" in rel_path:
             mode = "0750"
        else:
             # Whitelist for special executables
             executables = ["disable_selinux.sh", "daemon", "install-recovery", "rw-system.sh", "getSPL"]
             if any(exe in rel_path for exe in executables):
                 mode = "0755"
                 
    return uid, gid, mode

def patch_fs_config(target_dir: Path, fs_config_path: Path):
    """
    Main function: Patch fs_config
    """
    target_dir = Path(target_dir).resolve()
    fs_config_path = Path(fs_config_path).resolve()
    part_name = target_dir.name # e.g., mi_ext, vendor, system

    logger.info(f"Patching fs_config for {part_name}...")

    # 1. Read original config
    fs_data = load_fs_config(fs_config_path)
    logger.debug(f"Loaded {len(fs_data)} entries from origin.")

    prefix = f"{part_name}"
    
    logger.debug(f"Using prefix '{prefix}' for scan.")
    # ==========================

    new_entries = 0
    
    # 2. Scan directory and patch (pass prefix)
    all_files = scan_dir_recursive(target_dir, prefix=prefix)
    
    for path_key in all_files:
        if path_key in fs_data:
            continue
            
        # If not in original config, need to add
        # At this point path_key is already in "/mi_ext/etc/..." format
        
        # Calculate real file path for type checking
        # Remove prefix, restore to local path
        if prefix and path_key.startswith(prefix):
            local_rel = path_key[len(prefix):].lstrip("/")
        else:
            local_rel = path_key.lstrip("/")
            
        real_path = target_dir / local_rel
        
        # Prevent file deleted but still in list
        if not real_path.exists() and not real_path.is_symlink():
            continue

        is_dir = real_path.is_dir() and not real_path.is_symlink()
        is_link = real_path.is_symlink()
        
        uid, gid, mode = get_file_mode(path_key, is_dir, is_link)
        
        attrs = [uid, gid, mode]
        
        # If it is a link, need to read link target
        if is_link:
            try:
                link_target = os.readlink(real_path)
                attrs.append(link_target)
            except OSError:
                logger.warning(f"Failed to read link: {real_path}")
                continue

        # Write to dictionary
        fs_data[path_key] = attrs
        new_entries += 1
        logger.debug(f"Added: {path_key} -> {attrs}")

    # 3. Write back to file (sorted by path)
    logger.info(f"Added {new_entries} new entries to fs_config.")
    
    with open(fs_config_path, "w", encoding="utf-8", newline='\n') as f:
        for path in sorted(fs_data.keys()):
            line = f"{path} {' '.join(fs_data[path])}\n"
            f.write(line)
