# HyperOS Porting Tool (Python)

A Python-based tool for porting HyperOS ROMs to various devices. This tool automates many of the steps required for porting, including unpacking, patching, repacking, and signing.

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
- `otatools`: Android OTA tools (must be placed in the `otatools/` directory).
  - Due to size restrictions, `otatools` are not included in this repository. You can obtain them from standard Android build environments or other ROM porting resources.
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
   - Download or extract `otatools` (ensure it includes `bin/`, `lib64/`, `security/`, etc.).
   - Place the `otatools` directory in the root of the project:
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
- `otatools/`: (External) Android OTA tools required for operation.
- `out/`: Output directory for generated ROMs.

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request.

## License

[MIT License](LICENSE) (or specify your license)
