# 🚀 HyperOS 移植工具 (Python 版)

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Unlicense-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)](https://www.ubuntu.com/)

**中文 (Chinese)** | [English](README.md)

一个功能强大、自动化的 Python 移植工具，专为小米/红米设备的 HyperOS ROM 移植而设计。该工具涵盖了整个生命周期：解包、智能修补、功能恢复、重新打包和签名。

---

## 🌟 核心特性

- 🛠️ **全自动化**: 从底包/移植包 ZIP 到最终可刷入 ZIP 的端到端移植流程。
- 💉 **智能修补**: 自动修改固件、系统、框架和 ROM 属性。
- 🧬 **GKI 支持**: 针对 GKI 2.0 (5.10+) 及标准 GKI 设备，提供智能 KernelSU 注入。
- 🧩 **模块化配置**: 通过简单的 JSON 文件开启/关闭功能（AOD、AI 引擎等）。
- 🌏 **EU 本地化**: 为 Global/EU 底包恢复国内特有功能（NFC、小米钱包、小爱同学）。
- 📦 **多格式支持**: 支持生成 `payload.bin` (Recovery/OTA) 或 `super.img` (Hybrid/Fastboot) 格式。
- 🔒 **自动签名**: 自动为最终生成的 ZIP 文件签名，确保无缝安装。

---

## 📱 机型兼容性

### 支持机型
- 理论上支持内核版本 **5.10 及以上 (GKI 2.0+)** 的 **高通平台** 小米/红米设备。
- 支持在 `devices/<机型代码>/` 中自定义机型覆盖规则。

### 已验证机型
- **底包 (Stock):** 小米 13 (HyperOS 2.0/3.0)
- **移植来源:**
  - 小米 14 / 15 / 17
  - 红米 K90 / K90 Pro
  - 支持 HyperOS CN 3.0 正式版及测试版

---

## ⚙️ 前置条件

- **Python 3.8+**
- **Linux 环境** (推荐使用 Ubuntu 20.04+)
- **Sudo 权限** (用于挂载/卸载镜像)
- **OTA 工具**: 已内置在 `otatools/` 目录中。

---

## 🚀 快速开始

### 1. 安装
```bash
git clone https://github.com/yourusername/HyperOS-Port-Python.git
cd HyperOS-Port-Python
# 安装可选依赖
pip install -r requirements.txt 
```

### 2. 基本用法
准备好底包 (Stock ROM) 和移植包 (Port ROM) 的 ZIP 文件，然后运行：

**OTA/Recovery 模式 (默认):**
```bash
sudo python3 main.py --stock <底包路径> --port <移植包路径>
```

**Hybrid/Fastboot 模式 (Super Image):**
```bash
sudo python3 main.py --stock <底包路径> --port <移植包路径> --pack-type super
```

---

## 🛠️ 参数说明

### 常用命令行参数

| 参数 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `--stock` | **(必需)** 底包 (Stock ROM) 路径 | 无 |
| `--port` | **(必需)** 移植包 (Port ROM) 路径 | 无 |
| `--pack-type` | 打包格式: `payload` 或 `super` | `payload` |
| `--ksu` | 注入 KernelSU 到 `init_boot`/`boot` | `false` |
| `--work-dir` | 解包和修补的工作目录 | `build` |
| `--clean` | 开始前清理工作目录 | `false` |
| `--debug` | 开启调试日志 | `false` |
| `--eu-bundle` | EU 本地化资源包 (ZIP) 的路径或 URL | 无 |

---

## 🔧 配置系统

本项目采用模块化的 JSON 配置系统。

### 1. 特性开关 (`features.json`)
管理每个设备的系统特性和属性。
- **位置**: `devices/<机型代码>/features.json`

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

### 2. 资源 overlays (`replacements.json`)
自动化文件/目录替换（如 overlays、音频配置等）。
```json
[
    {
        "description": "系统 Overlays",
        "type": "file",
        "search_path": "product",
        "files": ["DevicesOverlay.apk"]
    }
]
```

---

## 🏮 EU 本地化 (恢复国内功能)

为 EU/Global ROM 恢复 **中国国内特有的功能** (NFC, 小米钱包, 小爱同学)，同时保持 "国际版" 伪装。

1. **启用**: 在 `features.json` 中设置 `"enable_eu_localization": true`。
2. **生成资源包**:
   ```bash
   python3 tools/generate_eu_bundle.py --rom <CN_ROM.zip> --config devices/common/eu_bundle_config.json
   ```
3. **应用**:
   ```bash
   sudo python3 main.py ... --eu-bundle eu_localization_bundle_v1.0.zip
   ```

---

## 📂 项目结构

```text
HyperOS-Port-Python/
├── src/               # 核心 Python 源代码
│   ├── core/          # 解包、修补、重打包逻辑
│   ├── modules/       # 专门的修改模块
│   └── utils/         # Shell 和文件工具
├── devices/           # 特定机型的配置和 overlay
├── otatools/          # Android OTA 二进制文件 (bin, lib64)
├── out/               # 最终生成的 ROM 输出目录
└── tools/             # 辅助工具 (Bundle 生成器等)
```

---

## 🤝 特别鸣谢

本项目大部分由 **Gemini Pro 3** 协助开发完成。

**特别感谢:**
- [HyperCeiler](https://github.com/ReChronoRain/HyperCeiler/)
- [OemPorts10T-PIF](https://github.com/Danda420/OemPorts10T-PIF)
- [FrameworkPatcher](https://github.com/FrameworksForge/FrameworkPatcher)
- [xiaomi.eu](https://xiaomi.eu)

---

## 📜 许可证

基于 [Unlicense](LICENSE) 发布。完全免费，可任意用于任何用途。
