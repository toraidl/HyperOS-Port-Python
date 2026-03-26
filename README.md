# 🚀 HyperOS 移植工具 (Python 版)

[![GitHub stars](https://img.shields.io/github/stars/toraidl/HyperOS-Port-Python?style=flat)](https://github.com/toraidl/HyperOS-Port-Python/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/toraidl/HyperOS-Port-Python?style=flat)](https://github.com/toraidl/HyperOS-Port-Python/network/members)

**中文 (Chinese)** | [English](README_EN.md)

一个面向小米/红米设备的 HyperOS ROM 移植工具。覆盖常见移植流程：解包、补丁处理、功能适配、重新打包与 OTA 升级包产出。

---

## 🌟 核心特性

- 🛠️ **流程自动化**: 从底包/移植包 ZIP 到可刷入产物的完整流程。
- 💉 **系统补丁**: 按规则修改固件、系统、框架和 ROM 属性。
- 🧬 **GKI 支持**: 针对 GKI 2.0 (5.10+) 及标准 GKI 设备提供 KernelSU 注入能力。
- 🔓 **AVB 禁用**: 通过直接修改 `vbmeta.img` 禁用 Android 验证启动 (AVB)，避免修改 fstab 导致的 DSU 无法使用问题。
- 🔐 **自定义 AVB 验证链**: 可基于 stock `vbmeta/vbmeta_system` 自动重建 AVB 拓扑，为目标镜像补齐 footer、重建 `vbmeta*.img` 并执行链路校验；同时对非动态分区应用物理分区上限保护，避免 OTA 写入超分区。
- 🚀 **Wild Boost（狂暴引擎）**: 将红米机型的狂暴引擎适配并移植到小米机型；要求内核版本一致，当前已验证小米 12S 与小米 13。
- 🧩 **模块化配置**: 通过简单的 JSON 文件开启/关闭功能（AOD、AI 引擎等）。
- 🌏 **EU 本地化**: 为 Global/EU 底包恢复国内特有功能（NFC、小米钱包、小爱同学）。
- 📦 **多格式支持**: 支持生成 `payload.bin` (Recovery/OTA) 或 `super.img` (Hybrid/Fastboot) 格式。
- 🔒 **官方 OTA 升级链路**: 产出兼容官方 OTA 格式的包，可通过官方升级 App 执行 AB 升级；出现问题时可回滚。

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

- **Python 3.10+**
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
# 创建本地虚拟环境
python3 -m venv .venv

# 安装开发和测试依赖
.venv/bin/python -m pip install -r requirements-dev.txt -r requirements-test.txt

# 运行测试
.venv/bin/python -m pytest -q

# 代码格式化
.venv/bin/python -m black src tests main.py

# 代码检查（与 CI 对齐）
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
  src/core/monitoring/console_ui.py \
  src/core/monitoring/plugin_integration.py \
  src/core/monitoring/workflow_integration.py \
  src/core/rom_metadata.py \
  src/core/tooling.py \
  src/core/workspace.py \
  src/core/modifiers/__init__.py \
  src/core/modifiers/base_modifier.py \
  src/core/modifiers/framework/base.py \
  src/core/modifiers/framework/modifier.py \
  src/core/modifiers/framework/tasks.py \
  src/core/modifiers/plugin_system.py \
  src/core/modifiers/plugins/feature_unlock.py \
  src/core/modifiers/smali_args.py \
  src/core/modifiers/transaction.py \
  src/core/modifiers/unified_modifier.py

# Curated 类型检查
.venv/bin/python -m mypy --config-file mypy-curated.ini
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
| `--preflight-only` | 仅执行预检并输出报告，然后退出 | `false` |
| `--skip-preflight` | 跳过预检阶段（不建议） | `false` |
| `--preflight-strict` | 将风险项也视为失败项（用于严格阻断） | `false` |
| `--preflight-report` | 预检 JSON 报告输出路径 | `build/preflight-report.json` |
| `--enable-snapshots` | 在关键阶段保存工作目录快照 | `false` |
| `--snapshot-dir` | 快照目录（未设置时使用 `<work-dir>/snapshots`） | `null` |
| `--rollback-to-snapshot` | 从指定快照恢复目标工作目录并退出 | `null` |
| `--enable-diff-report` | 生成产物差异报告（前后文件/属性/APK变化） | `false` |
| `--diff-report` | 差异报告 JSON 输出路径 | `build/diff-report.json` |
| `--custom-avb-chain` | 启用“自定义 AVB 验证链”（按 stock AVB 拓扑重建 footer/vbmeta 并校验） | `false` |
| `--resume-from-packer` | 从已保存的 repack 检查点恢复，直接进入打包阶段 | `false` |

---

## 🔧 配置系统

本项目采用模块化的 JSON 配置系统。

### 1. 自动设备配置 (Auto-Configuration)
工具支持从底包 (Stock ROM) 自动提取设备信息并创建设备配置。

- **触发条件**: 当 `devices/<机型代码>/` 目录不存在时自动触发
- **数据来源**: 通过 `payload-dumper --json` 提取分区信息和元数据
- **生成文件**:
  - `config.json` - 设备基本配置
  - `features.json` - 功能开关和属性
  - `replacements.json` - 资源替换规则
  - `partition_info.json` - 分区布局信息（动态分区列表、固件分区列表、super 大小）
  - 启用 `--custom-avb-chain` 时会自动补全 AVB 固化字段（`physical_partition_sizes`、`avb_*_partitions`、`avb_strict_partitions`）

**示例生成的 partition_info.json**:
```json
{
    "device_code": "myron",
    "super_size": 14485028864,
    "dynamic_partitions": [
        "odm", "product", "system", "system_dlkm",
        "system_ext", "vendor", "vendor_dlkm", "mi_ext"
    ],
    "firmware_partitions": ["abl", "aop", "boot", ...],
    "physical_partition_sizes": {
        "boot": 100663296,
        "recovery": 104857600
    },
    "avb_hash_partitions": ["boot", "dtbo", "..."],
    "avb_hashtree_partitions": ["system", "vendor", "..."],
    "avb_chain_partitions": [
        {"name": "boot", "rollback_index_location": 3}
    ],
    "avb_strict_partitions": ["boot", "recovery", "dtbo", "..."]
}
```

### 2. 设备配置 (`config.json`)
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

### 3. 狂暴引擎支持
根据内核版本自动安装性能增强模块。

**功能特性:**
- 📌 **自动检测**: 检测内核版本 (5.10 / 5.15)
- 📌 **智能安装**:
  - 内核 5.10: 安装到 `vendor_boot` ramdisk
  - 内核 5.15: 安装到 `vendor_dlkm`
- 📌 **AVB 自动禁用**: 防止修改后无法启动
- 📌 **设备伪装**: HexPatch `libmigui.so`
- 📌 **备用方案**: `persist.sys.feas.enable=true` 用于新系统

**支持设备:**
- 小米 12S (mayfly) - 内核 5.10
- 小米 13 (fuxi) - 内核 5.15

### 4. 特性开关 (`features.json`)
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

### 5. 资源 overlays (`replacements.json`)
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

为 xiaomi.eu ROM 恢复 **中国国内特有的功能** (NFC, 小米钱包, 小爱同学)，同时保持 "国际版" 伪装。

1. **启用**: 在 `features.json` 中设置 `"enable_eu_localization": true`。
2. **生成资源包**:
   ```bash
   # Generate ZIP bundle (Default)
   python3 tools/generate_eu_bundle.py --rom <CN_ROM.zip> --config devices/common/eu_bundle_config.json

   # Generate folder only (For manual modification)
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
| **Black** | 代码格式化 | `.venv/bin/python -m black src tests main.py` |
| **Ruff** | 快速 Python 检查 | `.venv/bin/python -m ruff check main.py tests src/app src/core/cache_manager.py src/core/packer.py src/core/conditions.py src/core/context.py src/core/props.py src/core/config_loader.py src/core/config_merger.py src/core/rom/package.py src/core/rom/extractors.py src/core/rom/utils.py src/core/monitoring/__init__.py src/core/monitoring/console_ui.py src/core/monitoring/plugin_integration.py src/core/monitoring/workflow_integration.py src/core/rom_metadata.py src/core/tooling.py src/core/workspace.py src/core/modifiers/__init__.py src/core/modifiers/base_modifier.py src/core/modifiers/framework/base.py src/core/modifiers/framework/modifier.py src/core/modifiers/framework/tasks.py src/core/modifiers/plugin_system.py src/core/modifiers/plugins/feature_unlock.py src/core/modifiers/smali_args.py src/core/modifiers/transaction.py src/core/modifiers/unified_modifier.py` |
| **MyPy（Curated）** | 重构运行链路的类型检查 | `.venv/bin/python -m mypy --config-file mypy-curated.ini` |

### 开发者自检（与 CI 一致）

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
  src/core/monitoring/console_ui.py \
  src/core/monitoring/plugin_integration.py \
  src/core/monitoring/workflow_integration.py \
  src/core/rom_metadata.py \
  src/core/tooling.py \
  src/core/workspace.py \
  src/core/modifiers/__init__.py \
  src/core/modifiers/base_modifier.py \
  src/core/modifiers/framework/base.py \
  src/core/modifiers/framework/modifier.py \
  src/core/modifiers/framework/tasks.py \
  src/core/modifiers/plugin_system.py \
  src/core/modifiers/plugins/feature_unlock.py \
  src/core/modifiers/smali_args.py \
  src/core/modifiers/transaction.py \
  src/core/modifiers/unified_modifier.py
.venv/bin/python -m mypy --config-file mypy-curated.ini
.venv/bin/python -m pytest -q
```

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

本项目主要由 AI 协作完成，包括 **Gemini 3.1 Pro**、**GPT-5.3**、**KM2.5**、**QWEN3.5** 等模型与助手。

**特别感谢:**
- [HyperCeiler](https://github.com/ReChronoRain/HyperCeiler/)
- [OemPorts10T-PIF](https://github.com/Danda420/OemPorts10T-PIF)
- [FrameworkPatcher](https://github.com/FrameworksForge/FrameworkPatcher)
- [xiaomi.eu](https://xiaomi.eu)

---

## 📜 许可证

基于 [Unlicense](LICENSE) 发布。完全免费，可任意用于任何用途。
