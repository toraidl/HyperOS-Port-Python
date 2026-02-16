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
   ```bash
   sudo python3 main.py --stock <path_to_stock_rom> --port <path_to_port_rom>
   ```

### Arguments

- `--stock`: Path to the Stock ROM (base ROM for the device).
- `--port`: Path to the Port ROM (HyperOS ROM to port).
- `--ksu`: (Optional) Inject KernelSU into init_boot.
- `--work-dir`: (Optional) Working directory (default: `build`).
- `--clean`: (Optional) Clean working directory before starting.
- `--debug`: (Optional) Enable debug logging.

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
