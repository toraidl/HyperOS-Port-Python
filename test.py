import subprocess
from pathlib import Path

# Locate libmigui.so in the build target directory
so_paths = list(Path("build/target/system_ext").rglob("libmigui.so"))

if not so_paths:
    print("libmigui.so not found!")
else:
    for so_file in so_paths:
        print(f"\n--- Checking {so_file} ---")
        # Extract printable strings from the shared object file
        cmd = ["strings", str(so_file)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            strings_output = result.stdout
            
            # Verify if target property strings are present
            if "ro.product.device" in strings_output:
                print("✅ Found: ro.product.device")
            else:
                print("❌ Not found: ro.product.device")
                
            if "ro.product.product.name" in strings_output:
                print("✅ Found: ro.product.product.name")
            else:
                print("❌ Not found: ro.product.product.name")
                
        except Exception as e:
            print(f"Execution error: {e}")

