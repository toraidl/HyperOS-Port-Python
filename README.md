# 🚀 HyperOS Porting Tool (Python)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Unlicense-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)](https://www.ubuntu.com/)

[中文 (Chinese)](README_CN.md) | **English**

A powerful, automated Python-based tool for porting HyperOS ROMs across Xiaomi/Redmi devices. This tool handles the entire lifecycle: unpacking, smart patching, feature restoration, repacking, and signing.

---

## 🌟 Key Features

- 🛠️ **Fully Automated**: End-to-end porting process from stock/port ZIPs to flashable output.
- 💉 **Smart Patching**: Automated modification of firmware, system, framework, and ROM properties.
- 🧬 **GKI Support**: Intelligent KernelSU injection for GKI 2.0 (5.10+) and standard GKI devices.
- 🔓 **Android 16 Ready**: Specialized `vendor_boot` fstab patching for KMI 6.12 to prevent fastboot bootloops.
- 🚀 **Wild Boost**: Auto-installation of performance modules with kernel version detection.
- 🧩 **Modular Configuration**: Toggle features (AOD, AI Engine, etc.) via simple JSON files.
- 🌏 **EU Localization**: Restore China-exclusive features (NFC, XiaoAi) to Global/EU bases.
- 📦 **Multi-Format Support**: Generate `payload.bin` (Recovery/OTA) or `super.img` (Hybrid/Fastboot) formats.
- 🔒 **Auto-Signing**: Automatically signs the final flashable ZIP for seamless installation.

---

## 📱 Compatibility

### Supported Devices
- Theoretically supports **Xiaomi/Redmi** devices with **Qualcomm** processors.
- Requires **Kernel version 5.10 or later** (GKI 2.0+).
- Custom overrides available in `devices/<device_code>/`.

### Wild Boost Compatible
- **Xiaomi 12S (mayfly)**: Kernel 5.10 - vendor_boot installation
- **Xiaomi 13 (fuxi)**: Kernel 5.15 - vendor_dlkm installation

### Tested & Verified
- **Base (Stock):**
  - Xiaomi 13 (HyperOS 2.0/3.0)
  - Xiaomi 12S (HyperOS 3.0 / A15)
  - Xiaomi 17 (HyperOS 3.0 / A16 / KMI 6.12)
- **Port Sources:**
  - Xiaomi 14 / 15 / 17
  - Redmi K90 / K90 Pro
  - Supports HyperOS CN 3.0 (Stable & Beta)

---

## ⚙️ Prerequisites

- **Python 3.10+**
- **Linux Environment** (Ubuntu 20.04+ recommended)
- **Sudo Access** (required for partition mounting/unmounting)
- **OTA Tools**: Included in the `otatools/` directory.

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone https://github.com/toraidl/HyperOS-Port-Python.git
cd HyperOS-Port-Python
# Install dependencies
pip install -r requirements.txt
```

### Development Setup (Optional)
If you want to contribute or run tests:
```bash
# Create a local virtual environment
python3 -m venv .venv

# Install development and test dependencies
.venv/bin/python -m pip install -r requirements-dev.txt -r requirements-test.txt

# Run tests
.venv/bin/python -m pytest -q

# Code formatting
.venv/bin/python -m black src tests main.py

# Linting
.venv/bin/python -m ruff check \
  main.py \
  tests \
  src/app \
  src/core/cache_manager.py \
  src/core/packer.py \
  src/core/conditions.py \
  src/core/context.py \
  src/core/props.py \
  src/core/config_loader.py \
  src/core/config_merger.py \
  src/core/rom/package.py \
  src/core/rom/extractors.py \
  src/core/rom/utils.py \
  src/core/monitoring/__init__.py \
  src/core/monitoring/plugin_integration.py \
  src/core/monitoring/workflow_integration.py \
  src/core/rom_metadata.py \
  src/core/tooling.py \
  src/core/workspace.py \
  src/core/modifiers/__init__.py \
  src/core/modifiers/base_modifier.py \
  src/core/modifiers/plugin_system.py \
  src/core/modifiers/smali_args.py \
  src/core/modifiers/transaction.py \
  src/core/modifiers/unified_modifier.py

# Curated type checking for the refactored runtime path
.venv/bin/python -m mypy --config-file mypy-curated.ini

```

### 2. Basic Usage
Prepare your Stock ROM and Port ROM ZIP files (or just Stock ROM for Official Modification), then run:

**OTA/Recovery Mode (Default):**
```bash
sudo python3 main.py --stock <path_to_stock_zip> --port <path_to_port_zip>
```

**Official Modification Mode (Modify Stock ROM only):**
```bash
sudo python3 main.py --stock <path_to_stock_zip>
```

**Hybrid/Fastboot Mode (Super Image):**
```bash
sudo python3 main.py --stock <path_to_stock_zip> --port <path_to_port_zip> --pack-type super
```

---

## 🛠️ Advanced Usage

### Arguments Reference

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--stock` | **(Required)** Path to the Stock ROM (Base) | N/A |
| `--port` | **(Optional)** Path to the Port ROM. If omitted, tool runs in **Official Modification mode**. | N/A |
| `--pack-type` | Output format: `payload` or `super` | from config |
| `--fs-type` | Filesystem type: `erofs` or `ext4` | from config |
| `--ksu` | Inject KernelSU into `init_boot`/`boot` | from config |
| `--work-dir` | Working directory for extraction/patching | `build` |
| `--clean` | Clean work directory before starting | `false` |
| `--debug` | Enable verbose debug logging | `false` |
| `--eu-bundle` | Path/URL to EU Localization Bundle ZIP | N/A |

---

## 🔧 Configuration System

The tool uses a modular JSON-based configuration system.

### 1. Device Configuration (`config.json`)
Control device-specific settings including wild_boost, pack type, and KSU.
- **Location**: `devices/<device_code>/config.json`
- **Priority**: CLI args > `config.json` > defaults

```json
{
    "wild_boost": {
        "enable": true
    },
    "pack": {
        "type": "payload",
        "fs_type": "erofs"
    },
    "ksu": {
        "enable": false
    }
}
```

**CLI Overrides:**
```bash
# Override pack type and filesystem
sudo python3 main.py --stock stock.zip --port port.zip --pack-type super --fs-type ext4
```

### 2. Wild Boost Support
Automatically installs performance boost modules based on kernel version.

**Features:**
- 📌 **Auto-detection**: Detects kernel version (5.10 / 5.15+)
- 📌 **Smart Installation**:
  - Kernel 5.10: Installs to `vendor_boot` ramdisk
  - Kernel 5.15+: Installs to `vendor_dlkm`
- 📌 **AVB Auto-disable**: Prevents bootloop after modification
- 📌 **Device Spoofing**: HexPatch for `libmigui.so`
- 📌 **Fallback**: `persist.sys.feas.enable=true` for newer systems

**Supported Devices:**
- Xiaomi 12S (mayfly) - Kernel 5.10
- Xiaomi 13 (fuxi) - Kernel 5.15

### 3. Feature Toggles (`features.json`)
Manage system features and properties per device.
- **Location**: `devices/<device_code>/features.json`

```json
{
    "xml_features": {
        "support_AI_display": true,
        "support_wild_boost": true
    },
    "build_props": {
        "product": { "ro.product.spoofed.name": "vermeer" }
    }
}
```

### 4. Resource Overlays (`replacements.json`)
Automate file/directory replacements (e.g., overlays, audio configs).
```json
[
    {
        "description": "System Overlays",
        "type": "file",
        "search_path": "product",
        "files": ["DevicesOverlay.apk"]
    }
]
```

---

## 🏮 EU Localization (China Feature Restoration)

Restores **China-exclusive features** (NFC, Mi Wallet, XiaoAi) to EU/Global ROMs while maintaining "International" status.

1. **Enable**: Set `"enable_eu_localization": true` in `features.json`.
2. **Generate Bundle**:
   ```bash
   python3 tools/generate_eu_bundle.py --rom <CN_ROM.zip> --config devices/common/eu_bundle_config.json
   ```
3. **Apply**:
   ```bash
   sudo python3 main.py ... --eu-bundle eu_localization_bundle_v1.0.zip
   ```

---

## 📂 Project Structure

```text
HyperOS-Port-Python/
├── src/                       # Core Python source code
│   ├── core/                  # Core ROM processing logic
│   │   ├── modifiers/         # ROM modification system
│   │   │   ├── framework/     # Framework-level patches (modular)
│   │   │   │   ├── patches.py     # Smali patch definitions
│   │   │   │   ├── base.py        # Framework modifier base class
│   │   │   │   ├── tasks.py       # Specific modification tasks
│   │   │   │   └── modifier.py    # Main framework modifier
│   │   │   └── plugins/       # Plugin system for APK modifications
│   │   ├── rom/               # ROM package handling (modular)
│   │   │   ├── package.py     # RomPackage class
│   │   │   ├── extractors.py  # ROM extraction methods
│   │   │   ├── utils.py       # ROM utilities
│   │   │   └── constants.py   # Partition lists and enums
│   │   ├── packer.py          # Image repacking logic
│   │   ├── context.py         # Porting context management
│   │   └── props.py           # Property management
│   ├── modules/               # APK-level modification modules
│   └── utils/                 # Shell and file utilities
│       ├── lpunpack.py        # Python implementation of lpunpack for compatibility
│       └── ...
├── devices/                   # Device-specific configs & overlays
├── otatools/                  # Android OTA binaries (bin, lib64)
├── tests/                     # Unit tests
├── out/                       # Final generated ROM outputs
├── tools/                     # Auxiliary tools
├── requirements.txt           # Production dependencies
├── requirements-dev.txt       # Development dependencies
└── pyproject.toml            # Python project configuration
```

---

## 🧪 Testing

Run the test suite with pytest:

```bash
# Run all tests
.venv/bin/python -m pytest -q

# Run specific test file
.venv/bin/python -m pytest tests/core/test_config_loader.py -q

# Run with coverage
.venv/bin/python -m pytest tests/ --cov=src --cov-report=html
```

---

## 🎨 Code Quality

This project uses several tools to maintain code quality:

| Tool | Purpose | Command |
|------|---------|---------|
| **Black** | Code formatting | `.venv/bin/python -m black src tests main.py` |
| **Ruff** | Fast Python linting | `.venv/bin/python -m ruff check main.py tests src/app src/core/cache_manager.py src/core/packer.py src/core/conditions.py src/core/context.py src/core/props.py src/core/config_loader.py src/core/config_merger.py src/core/rom/package.py src/core/rom/extractors.py src/core/rom/utils.py src/core/monitoring/__init__.py src/core/monitoring/plugin_integration.py src/core/monitoring/workflow_integration.py src/core/rom_metadata.py src/core/tooling.py src/core/workspace.py src/core/modifiers/__init__.py src/core/modifiers/base_modifier.py src/core/modifiers/plugin_system.py src/core/modifiers/smali_args.py src/core/modifiers/transaction.py src/core/modifiers/unified_modifier.py` |
| **MyPy (Curated)** | Type checking for the refactored runtime and the cleaned modifier orchestration modules | `.venv/bin/python -m mypy --config-file mypy-curated.ini` |

### Developer Self-Check

Run the same checks as CI before opening a pull request:

```bash
.venv/bin/python -m compileall -q src tests main.py
.venv/bin/python -m ruff check \
  main.py \
  tests \
  src/app \
  src/core/cache_manager.py \
  src/core/packer.py \
  src/core/conditions.py \
  src/core/context.py \
  src/core/props.py \
  src/core/config_loader.py \
  src/core/config_merger.py \
  src/core/rom/package.py \
  src/core/rom/extractors.py \
  src/core/rom/utils.py \
  src/core/monitoring/__init__.py \
  src/core/monitoring/plugin_integration.py \
  src/core/monitoring/workflow_integration.py \
  src/core/rom_metadata.py \
  src/core/tooling.py \
  src/core/workspace.py \
  src/core/modifiers/__init__.py \
  src/core/modifiers/base_modifier.py \
  src/core/modifiers/plugin_system.py \
  src/core/modifiers/smali_args.py \
  src/core/modifiers/transaction.py \
  src/core/modifiers/unified_modifier.py
.venv/bin/python -m mypy --config-file mypy-curated.ini
.venv/bin/python -m pytest -q
```

### Pre-commit Hooks (Optional)

Install pre-commit hooks to automatically check code quality before each commit:

```bash
# Install pre-commit
pip install pre-commit

# Install hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and ensure code quality checks pass
5. Commit your changes (`git commit -m 'feat: Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

---

## 🤝 Acknowledgments

Developed with the assistance of **Gemini Pro 3**.

**Special Thanks:**
- [HyperCeiler](https://github.com/ReChronoRain/HyperCeiler/)
- [OemPorts10T-PIF](https://github.com/Danda420/OemPorts10T-PIF)
- [FrameworkPatcher](https://github.com/FrameworksForge/FrameworkPatcher)
- [xiaomi.eu](https://xiaomi.eu)

---

## 📜 License

Released under the [Unlicense](LICENSE). Completely free for any use.
