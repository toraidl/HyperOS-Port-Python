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
   ```bash
   sudo python3 main.py --stock <底包路径> --port <移植包路径>
   ```

### 参数

- `--stock`：Stock ROM 的路径（设备的基础 ROM）。
- `--port`：Port ROM 的路径（要移植的 HyperOS ROM）。
- `--ksu`：（可选）将 KernelSU 注入 init_boot。
- `--work-dir`：（可选）工作目录（默认：`build`）。
- `--clean`：（可选）开始前清理工作目录。
- `--debug`：（可选）启用调试日志。

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
