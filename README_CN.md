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
- 🔓 **Android 16 支持**: 针对 KMI 6.12 提供专用的 `vendor_boot` fstab 修补，跳过标准 VBMETA 以防止 Fastboot 卡死。
- 🚀 **Wild Boost**: 自动安装性能增强模块，支持内核版本检测。
- 🧩 **模块化配置**: 通过简单的 JSON 文件开启/关闭功能（AOD、AI 引擎等）。
- 🌏 **EU 本地化**: 为 Global/EU 底包恢复国内特有功能（NFC、小米钱包、小爱同学）。
- 📦 **多格式支持**: 支持生成 `payload.bin` (Recovery/OTA) 或 `super.img` (Hybrid/Fastboot) 格式。
- 🔒 **自动签名**: 自动为最终生成的 ZIP 文件签名，确保无缝安装。

---

## 📱 机型兼容性

### 支持机型
- 理论上支持内核版本 **5.10 及以上 (GKI 2.0+)** 的 **高通平台** 小米/红米设备。
- 支持在 `devices/<机型代码>/` 中自定义机型覆盖规则。

### Wild Boost 兼容设备
- **小米 12S (mayfly)**: 内核 5.10 - 安装到 vendor_boot ramdisk
- **小米 13 (fuxi)**: 内核 5.15 - 安装到 vendor_dlkm

### 已验证机型
- **底包 (Stock):**
  - 小米 13 (HyperOS 2.0/3.0)
  - 小米 12S (HyperOS 3.0 / A15)
  - 小米 17 (HyperOS 3.0 / A16 / KMI 6.12)
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
# 安装依赖
pip install -r requirements.txt
```

### 开发环境设置 (可选)
如果你想参与贡献或运行测试：
```bash
# 安装开发依赖
pip install -r requirements-dev.txt

# 运行测试
pytest tests/ -v

# 代码格式化
black src/ --line-length 100

# 代码检查
ruff check src/
```

### 2. 基本用法
准备好底包 (Stock ROM) 和移植包 (Port ROM) 的 ZIP 文件（官改模式仅需底包），然后运行：

**OTA/Recovery 模式 (默认):**
```bash
sudo python3 main.py --stock <底包路径> --port <移植包路径>
```

**官改模式 (仅修改底包):**
```bash
sudo python3 main.py --stock <底包路径>
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
| `--port` | **(可选)** 移植包 (Port ROM) 路径。如果省略，则运行 **官改模式**。 | 无 |
| `--pack-type` | 打包格式: `payload` 或 `super` | `payload` |
| `--fs-type` | 文件系统类型：`erofs` 或 `ext4` | 从 config 读取 |
| `--ksu` | 注入 KernelSU 到 `init_boot`/`boot` | 从 config 读取 |
| `--work-dir` | 解包和修补的工作目录 | `build` |
| `--clean` | 开始前清理工作目录 | `false` |
| `--debug` | 开启调试日志 | `false` |
| `--eu-bundle` | EU 本地化资源包 (ZIP) 的路径或 URL | 无 |

---

## 🔧 配置系统

本项目采用模块化的 JSON 配置系统。

### 1. 设备配置 (`config.json`)
控制设备特定的设置，包括 wild_boost、打包类型和 KSU。
- **位置**: `devices/<机型代码>/config.json`
- **优先级**: CLI 参数 > `config.json` > 默认值

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

**CLI 覆盖示例:**
```bash
# 覆盖打包类型和文件系统
sudo python3 main.py --stock stock.zip --port port.zip --pack-type super --fs-type ext4
```

### 2. Wild Boost 支持
根据内核版本自动安装性能增强模块。

**功能特性:**
- 📌 **自动检测**: 检测内核版本 (5.10 / 5.15+)
- 📌 **智能安装**:
  - 内核 5.10: 安装到 `vendor_boot` ramdisk
  - 内核 5.15+: 安装到 `vendor_dlkm`
- 📌 **AVB 自动禁用**: 防止修改后无法启动
- 📌 **设备伪装**: HexPatch `libmigui.so`
- 📌 **备用方案**: `persist.sys.feas.enable=true` 用于新系统

**支持设备:**
- 小米 12S (mayfly) - 内核 5.10
- 小米 13 (fuxi) - 内核 5.15

### 3. 特性开关 (`features.json`)
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

### 4. 资源 overlays (`replacements.json`)
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
   # 生成 ZIP 包 (默认)
   python3 tools/generate_eu_bundle.py --rom <CN_ROM.zip> --config devices/common/eu_bundle_config.json

   # 仅生成文件夹 (方便手动添加缺失的 APK)
   python3 tools/generate_eu_bundle.py --rom <CN_ROM.zip> --config devices/common/eu_bundle_config.json --no-zip
   ```
3. **应用**:
   ```bash
   sudo python3 main.py ... --eu-bundle eu_localization_bundle_v1.0.zip
   ```

---

## 📂 项目结构

```text
HyperOS-Port-Python/
├── src/                       # 核心 Python 源代码
│   ├── core/                  # 核心 ROM 处理逻辑
│   │   ├── modifiers/         # ROM 修改系统
│   │   │   ├── framework/     # 框架级补丁 (模块化)
│   │   │   │   ├── patches.py     # Smali 补丁定义
│   │   │   │   ├── base.py        # 框架修改器基类
│   │   │   │   ├── tasks.py       # 具体修改任务
│   │   │   │   └── modifier.py    # 主框架修改器
│   │   │   └── plugins/       # APK 修改插件系统
│   │   ├── rom/               # ROM 包处理 (模块化)
│   │   │   ├── package.py     # RomPackage 类
│   │   │   ├── extractors.py  # ROM 提取方法
│   │   │   ├── utils.py       # ROM 工具函数
│   │   │   └── constants.py   # 分区列表和枚举
│   │   ├── packer.py          # 镜像重打包逻辑
│   │   ├── context.py         # 移植上下文管理
│   │   └── props.py           # 属性管理
│   ├── modules/               # APK 级别修改模块
│   └── utils/                 # Shell 和文件工具
│       ├── lpunpack.py        # lpunpack 的 Python 实现，增强兼容性
│       └── ...
├── devices/                   # 特定机型的配置和 overlay
├── otatools/                  # Android OTA 二进制文件 (bin, lib64)
├── tests/                     # 单元测试
├── out/                       # 最终生成的 ROM 输出目录
├── tools/                     # 辅助工具
├── requirements.txt           # 生产环境依赖
├── requirements-dev.txt       # 开发环境依赖
└── pyproject.toml            # Python 项目配置
```

---

## 🧪 测试

使用 pytest 运行测试套件：

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试文件
pytest tests/core/test_config_loader.py -v

# 运行并生成覆盖率报告
pytest tests/ --cov=src --cov-report=html
```

---

## 🎨 代码质量

本项目使用以下工具维护代码质量：

| 工具 | 用途 | 命令 |
|------|------|------|
| **Black** | 代码格式化 | `black src/ --line-length 100` |
| **Ruff** | 快速 Python 检查 | `ruff check src/` |
| **MyPy** | 类型检查 | `mypy src/ --ignore-missing-imports` |

### 预提交钩子 (可选)

安装预提交钩子以在每次提交前自动检查代码质量：

```bash
# 安装 pre-commit
pip install pre-commit

# 安装钩子
pre-commit install

# 手动运行所有文件检查
pre-commit run --all-files
```

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/新功能`)
3. 提交你的修改
4. 确保测试通过且代码质量检查通过
5. 提交更改 (`git commit -m 'feat: 添加新功能'`)
6. 推送到分支 (`git push origin feature/新功能`)
7. 创建 Pull Request

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
