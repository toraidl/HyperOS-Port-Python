# 🚀 HyperOS Porting Tool (Python)

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Unlicense-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)](https://www.ubuntu.com/)

[中文 (Chinese)](README_CN.md) | **English**

A powerful, automated Python-based tool for porting HyperOS ROMs across Xiaomi/Redmi devices. This tool handles the entire lifecycle: unpacking, smart patching, feature restoration, repacking, and signing.

---

## 🌟 Key Features

- 🛠️ **Fully Automated**: End-to-end porting process from stock/port ZIPs to flashable output.
- 💉 **Smart Patching**: Automated modification of firmware, system, framework, and ROM properties.
- 🧬 **GKI Support**: Intelligent KernelSU injection for GKI 2.0 (5.10+) and standard GKI devices.
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

### Tested & Verified
- **Base (Stock):** Xiaomi 13 (HyperOS 2.0/3.0)
- **Port Sources:**
  - Xiaomi 14 / 15 / 17
  - Redmi K90 / K90 Pro
  - Supports HyperOS CN 3.0 (Stable & Beta)

---

## ⚙️ Prerequisites

- **Python 3.8+**
- **Linux Environment** (Ubuntu 20.04+ recommended)
- **Sudo Access** (required for partition mounting/unmounting)
- **OTA Tools**: Included in the `otatools/` directory.

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone https://github.com/yourusername/HyperOS-Port-Python.git
cd HyperOS-Port-Python
# Install any optional dependencies
pip install -r requirements.txt 
```

### 2. Basic Usage
Prepare your Stock ROM and Port ROM ZIP files, then run:

**OTA/Recovery Mode (Default):**
```bash
sudo python3 main.py --stock <path_to_stock_zip> --port <path_to_port_zip>
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
| `--port` | **(Required)** Path to the Port ROM (Source) | N/A |
| `--pack-type` | Output format: `payload` or `super` | `payload` |
| `--ksu` | Inject KernelSU into `init_boot`/`boot` | `false` |
| `--work-dir` | Working directory for extraction/patching | `build` |
| `--clean` | Clean work directory before starting | `false` |
| `--debug` | Enable verbose debug logging | `false` |
| `--eu-bundle` | Path/URL to EU Localization Bundle ZIP | N/A |

---

## 🔧 Configuration System

The tool uses a modular JSON-based configuration system.

### 1. Feature Toggles (`features.json`)
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

### 2. Resource Overlays (`replacements.json`)
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
├── src/               # Core Python source code
│   ├── core/          # Unpacking, Patching, Repacking logic
│   ├── modules/       # Specialized modification modules
│   └── utils/         # Shell and File utilities
├── devices/           # Device-specific configs & overlays
├── otatools/          # Android OTA binaries (bin, lib64)
├── out/               # Final generated ROM outputs
└── tools/             # Auxiliary tools (Bundle generator, etc.)
```

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
