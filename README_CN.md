# HyperOS 移植工具 (Python 版)

**本项目由 gemini pro + opencode + Antigravity 完成，根据 https://github.com/toraidl/hyperos_port 改编而来。**

[English](README.md)

这是一个基于 Python 的工具，用于将 HyperOS ROM 移植到各种设备。该工具自动化了移植所需的许多步骤，包括解包、修补、重新打包和签名。

## 支持机型

- 理论上支持内核版本 5.10 及以上的高通平台小米/红米设备。
- 具体机型可能需要修改 `devices/<机型代码>/override/` 目录下的文件。

### 已测试成功

- **底包机型:** 小米 13 (官方 HyperOS 2.0/3.0)
- **移植包来源:**
  - 小米 14
  - 小米 15
  - 小米 17
  - 红米 K90 / K90 Pro
  - (支持最新 HyperOS CN 3.0 正式版及测试版)

## 功能特性

- **自动移植**：简化 HyperOS ROM 的移植流程。
- **固件修改**：包含用于修改固件、系统、框架和 ROM 属性的模块。
- **APK 修补**：根据需要修补系统 APK。
- **重新打包**：将修改后的系统重新打包为可刷入的 ZIP 文件。
- **签名**：为输出的 ZIP 文件签名以供安装。
- **多设备支持**：可针对不同设备进行配置（参见 `devices/` 目录）。
- **特性配置**：支持通过 JSON 配置模块化开启/关闭系统特性（如 AOD、AI 引擎等）。

## 特性配置 (Feature Configuration)

本项目使用基于 JSON 的配置系统来管理设备特性和系统属性。

- **通用配置**: `devices/common/features.json`
  - 定义所有设备通用的特性（例如 PIF 伪装、WildBoost 狂暴引擎）。
- **机型配置**: `devices/<机型代码>/features.json`
  - 覆盖通用设置或添加特定机型的属性（例如机型伪装）。

`features.json` 示例：
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

### 资源替换 (Files & Dirs)

文件和目录的替换规则（如 overlays, 音频配置等）通过 `replacements.json` 管理。

- **通用替换**: `devices/common/replacements.json`
- **机型替换**: `devices/<机型代码>/replacements.json` (追加到通用列表)

`replacements.json` 示例:
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

## 前置条件

- Python 3.8+
- Linux 环境（在 Ubuntu 20.04+ 上测试通过）
- `otatools`：Android OTA 工具（已包含在 `otatools/` 目录中）。
  - 这些工具是解包和重新打包 ROM 所必需的。
- `sudo` 权限（用于挂载/卸载镜像）

## 安装

1. 克隆仓库：
   ```bash
   git clone https://github.com/yourusername/HyperOS-Port-Python.git
   cd HyperOS-Port-Python
   ```

2. 安装依赖（如果有）：
   ```bash
   pip install -r requirements.txt
   ```
   *注意：本项目主要使用 Python 标准库，但请检查是否有特定要求。*

3. 设置 `otatools`：
   - `otatools` 已经包含在仓库中。除非您需要特定版本，否则无需手动下载。
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

## 使用方法

1. 准备您的 Stock ROM（底包）和 Port ROM（移植包）（作为 zip 文件或目录）。
2. 运行工具：

   **默认模式 (OTA Recovery/Payload.bin):**
   ```bash
   sudo python3 main.py --stock <底包路径> --port <移植包路径>
   ```

   **Hybrid 模式 (Recovery/Fastboot):**
   ```bash
   sudo python3 main.py --stock <底包路径> --port <移植包路径> --pack-type super
   ```

### 参数

- `--stock`：Stock ROM 的路径（设备的基础 ROM）。
- `--port`：Port ROM 的路径（要移植的 HyperOS ROM）。
- `--pack-type`：（可选）打包类型：`payload`（默认，适用于 Recovery/OTA）或 `super`（适用于 Hybrid Recovery/Fastboot）。
- `--ksu`：（可选）将 KernelSU 注入 init_boot。
- `--work-dir`：（可选）工作目录（默认：`build`）。
- `--clean`：（可选）开始前清理工作目录。
- `--debug`：（可选）启用调试日志。
- `--eu-bundle`: (可选) EU 本地化资源包的路径或 URL。

## EU 本地化 (恢复国内功能)

此功能旨在为 EU/Global ROM 恢复 **中国国内特有的功能**（如 NFC 门卡、小米钱包、小爱同学等），同时保持 "国际版" 伪装以通过安全检查。

### 如何开启

1.  **自动开启**: 如果移植包 (Port ROM) 被识别为 `xiaomi.eu` 版本（基于文件名或构建主机名），本地化属性将自动应用。
2.  **手动开启**: 在您的 `devices/<机型代码>/features.json` 中添加 `"enable_eu_localization": true`。

### 如何应用应用包 (智能替换)

为了注入实际的国内版应用（由于体积过大未包含在 git 中），您需要生成并提供一个 **资源包 (Bundle)**。

1.  **制作资源包**:
    *   准备一个“供体” CN ROM（例如官方 HyperOS CN zip）。
    *   运行生成工具：
    ```bash
    # 使用默认配置: devices/common/eu_bundle_config.json
    python3 tools/generate_eu_bundle.py --rom <CN_ROM路径.zip> --config devices/common/eu_bundle_config.json
    ```
    *   输出: `eu_localization_bundle_v1.0.zip`

2.  **在移植时应用**:
    ```bash
    sudo python3 main.py ... --eu-bundle eu_localization_bundle_v1.0.zip
    ```
    *工具将自动删除冲突的国际版应用，并注入资源包中的国内版应用。*

## 目录结构

- `src/`：工具的源代码。
  - `core/`：核心逻辑（修补、重打包、ROM 处理）。
  - `modules/`：针对系统不同部分的特定模块。
  - `utils/`：实用脚本（Shell 执行、文件操作）。
- `devices/`：特定设备的配置和覆盖文件。
- `otatools/`：操作所需的 Android OTA 工具。
- `out/`：生成的 ROM 的输出目录。

## 贡献

欢迎贡献！请 fork 仓库并提交 pull request。

## 致谢

本项目大部分由 **Gemini Pro 3** 协助开发完成。

特别感谢：
1. https://github.com/ReChronoRain/HyperCeiler/
2. https://github.com/Danda420/OemPorts10T-PIF
3. https://github.com/FrameworksForge/FrameworkPatcher
4. xiaomi.eu

## 许可证

本项目基于 [Unlicense](LICENSE) 发布。完全免费，可以任意复制。
