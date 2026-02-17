# HyperOS Porting Tool (Python)

**This project was completed by gemini pro + opencode + Antigravity, adapted from https://github.com/toraidl/hyperos_port.**

[中文 (Chinese)](README_CN.md)

A Python-based tool for porting HyperOS ROMs to various devices. This tool automates many of the steps required for porting, including unpacking, patching, repacking, and signing.

## Supported Devices

- Theoretically supports Xiaomi/Redmi devices with Qualcomm processors and kernel version 5.10 or later.
- Specific devices may require modifications in `devices/<device_code>/override/`.

### Successfully Tested

- **Base Device (Stock):** Xiaomi 13 (Official HyperOS 2.0/3.0)
- **Port ROM Source:**
  - Xiaomi 14
  - Xiaomi 15
  - Xiaomi 17
  - Redmi K90 / K90 Pro
  - (Supports latest HyperOS CN 3.0 Stable and Beta versions)

## Features

- **Automated Porting**: Streamlines the process of porting HyperOS ROMs.
- **Firmware Modification**: Includes modules for modifying firmware, system, framework, and ROM properties.
- **APK Patching**: Patches system APKs as needed.
- **Repacking**: Repacks the modified system into a flashable ZIP file.
- **Signing**: Signs the output ZIP for installation.
- **Multi-Device Support**: Configurable for different devices (see `devices/` directory).
- **Feature Configuration**: Support for modular feature toggles (AOD, AI Engine, etc.) via JSON configuration.

## Feature Configuration

The project uses a JSON-based configuration system to manage device features and system properties.

- **Common Configuration**: `devices/common/features.json`
  - Defines global features enabled for all devices (e.g., PIF spoofing, WildBoost).
- **Device Configuration**: `devices/<device_code>/features.json`
  - Overrides common settings or adds device-specific properties (e.g., Device Spoofing).

Example `features.json`:
```json
{
    "xml_features": {
        "support_AI_display": true,
        "support_wild_boost": true
    },
    "build_props": {
        "product": {
            "ro.product.spoofed.name": "vermeer"
        }
    }
}
```

### Resource Replacements (Files & Dirs)

File and directory replacements (like overlays, audio configs) are managed via `replacements.json`.

- **Common Replacements**: `devices/common/replacements.json`
- **Device Replacements**: `devices/<device_code>/replacements.json` (Appends to common list)

Example `replacements.json`:
```json
[
    {
        "description": "System Overlays",
        "type": "file",
        "search_path": "product",
        "files": ["DevicesOverlay.apk"]
    },
    {
        "description": "Custom Audio",
        "type": "dir",
        "search_path": "product",
        "ensure_exists": true,
        "files": ["MiSound"]
    }
]
```

## Prerequisites

- Python 3.8+
- Linux environment (tested on Ubuntu 20.04+)
- `otatools`: Android OTA tools (included in `otatools/` directory).
  - These tools are required for unpacking and repacking ROMs.
- `sudo` access (for mounting/unmounting images)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/HyperOS-Port-Python.git
   cd HyperOS-Port-Python
   ```

2. Install dependencies (if any):
   ```bash
   pip install -r requirements.txt
   ```
   *Note: This project primarily uses the Python Standard Library, but check for any specific requirements.*

3. Setup `otatools`:
   - `otatools` are already included in the repository. No manual download is required unless you need a specific version.
     ```
     HyperOS-Port-Python/
     ├── otatools/
     │   ├── bin/
     │   ├── lib64/
     │   └── ...
     ├── src/
     ├── main.py
     └── ...
     ```

## Usage

1. Prepare your Stock ROM and Port ROM (as zip files or directories).
2. Run the tool:

   **Default (OTA Recovery/Payload.bin):**
   ```bash
   sudo python3 main.py --stock <path_to_stock_rom> --port <path_to_port_rom>
   ```

   **Hybrid (Recovery/Fastboot):**
   ```bash
   sudo python3 main.py --stock <path_to_stock_rom> --port <path_to_port_rom> --pack-type super
   ```

### Arguments

- `--stock`: Path to the Stock ROM (base ROM for the device).
- `--port`: Path to the Port ROM (HyperOS ROM to port).
- `--pack-type`: (Optional) Output format: `payload` (default, for Recovery/OTA) or `super` (for Hybrid Recovery/Fastboot).
- `--ksu`: (Optional) Inject KernelSU into init_boot.
- `--work-dir`: (Optional) Working directory (default: `build`).
- `--clean`: (Optional) Clean working directory before starting.
- `--debug`: (Optional) Enable debug logging.
- `--eu-bundle`: (Optional) Path or URL to a EU Localization Bundle zip.

## EU Localization (China Feature Restoration)

This feature restores **China-exclusive features** (NFC, Mi Wallet, XiaoAi, etc.) to EU/Global ROMs while maintaining "International" status to pass safety checks.

### How to Enable

1.  **Automatic**: If the Port ROM is detected as `xiaomi.eu` (based on filename or build host), localization properties are applied automatically.
2.  **Manual**: Add `"enable_eu_localization": true` to your `devices/<code/features.json`.

### How to Apply Apps (Smart Replacement)

To inject the actual Chinese apps (which are large and not included in git), you must generate and provide a **Bundle**.

1.  **Generate a Bundle**:
    *   Prepare a Donor CN ROM (e.g., official HyperOS CN zip).
    *   Run the generator tool:
    ```bash
    # Uses default config: devices/common/eu_bundle_config.json
    python3 tools/generate_eu_bundle.py --rom <path_to_cn_rom.zip> --config devices/common/eu_bundle_config.json
    ```
    *   Output: `eu_localization_bundle_v1.0.zip`

2.  **Apply during Porting**:
    ```bash
    sudo python3 main.py ... --eu-bundle eu_localization_bundle_v1.0.zip
    ```
    *The tool will intelligently remove conflicting Global apps and inject the CN versions from the bundle.*

## Directory Structure

- `src/`: Source code for the tool.
  - `core/`: Core logic (patching, repacking, ROM handling).
  - `modules/`: Specific modules for different parts of the system.
  - `utils/`: Utility scripts (shell execution, file manipulation).
- `devices/`: Device-specific configurations and overlays.
- `otatools/`: Android OTA tools required for operation.
- `out/`: Output directory for generated ROMs.

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request.

## Acknowledgments

This project was largely developed with the assistance of **Gemini Pro 3**.

Special thanks to:
1. https://github.com/ReChronoRain/HyperCeiler/
2. https://github.com/Danda420/OemPorts10T-PIF
3. https://github.com/FrameworksForge/FrameworkPatcher
4. xiaomi.eu

## License

This project is released under the [Unlicense](LICENSE). It is completely free and can be arbitrarily copied.
