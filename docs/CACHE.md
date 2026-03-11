# Port ROM Cache机制

## 概述

Port ROM Cache机制是一个分层缓存系统，用于加速多机型ROM移植过程。它允许同一个Port ROM的处理结果（提取的分区和修改的APK）被多个不同机型的移植任务复用，显著减少重复工作。

## 特性

- **分层缓存**: 支持分区级缓存（Level 1）和APK修改缓存（Level 2）
- **文件锁支持**: 并发安全，支持多进程同时访问
- **版本控制**: 基于ROM哈希和修改器版本的自动缓存失效
- **完整性验证**: 自动验证缓存完整性，支持修复

## 使用方法

### 基本用法

默认情况下，缓存是启用的：

```bash
python main.py --stock stock.zip --port port.zip
```

### CLI参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--cache-dir` | 缓存目录路径 | `.cache/portroms` |
| `--no-cache` | 禁用缓存，强制完整提取和修改 | - |
| `--clear-cache` | 开始前清除所有缓存 | - |
| `--show-cache-stats` | 显示缓存统计并退出 | - |

### 查看缓存统计

```bash
python main.py --show-cache-stats
```

输出示例：
```json
{
  "version": "1.0",
  "cache_root": ".cache/portroms",
  "total_size_bytes": 2147483648,
  "total_size_mb": 2048.0,
  "cached_roms": [
    {
      "hash": "a1b2c3d4...",
      "partitions": [
        {"name": "system", "size_mb": 1024.0},
        {"name": "product", "size_mb": 512.0}
      ],
      "total_size_bytes": 1610612736
    }
  ]
}
```

### 禁用缓存

```bash
python main.py --stock stock.zip --port port.zip --no-cache
```

### 清除缓存

```bash
# 清除所有缓存
python main.py --stock stock.zip --port port.zip --clear-cache

# 或使用专用工具
python tools/cache_manager.py clean --all
```

## 缓存工具

### 查看缓存列表

```bash
python tools/cache_manager.py list
```

### 查看详细统计

```bash
python tools/cache_manager.py stats
```

### 验证缓存完整性

```bash
# 仅验证
python tools/cache_manager.py verify

# 验证并修复
python tools/cache_manager.py verify --fix
```

## 缓存结构

```
.cache/portroms/
├── metadata.json              # 全局缓存元数据
├── a1b2c3d4e5f6.../          # ROM哈希目录（前16字符）
│   ├── partitions/           # 分区缓存
│   │   ├── system/          # system分区
│   │   │   ├── app/
│   │   │   ├── framework/
│   │   │   └── cache_metadata.json
│   │   ├── product/         # product分区
│   │   └── ...
│   └── apks/                 # APK修改缓存
│       └── MIUIPackageInstaller_<hash>_InstallerModifier_v1.0/
│           ├── modified.apk
│           └── metadata.json
└── ...
```

## 工作原理

### 分区缓存流程

```
提取阶段:
  1. 检查全局缓存中是否有所需分区
  2. 如有，直接从缓存恢复到工作目录
  3. 如无，正常提取并保存到缓存

修改阶段:
  1. 在修改后的分区上执行修改
  2. （未来）保存修改后的完整分区到缓存
```

### APK缓存流程

```
APK修改:
  1. 计算APK缓存键（APK哈希+修改器类名+版本）
  2. 检查缓存中是否存在修改后的APK
  3. 如有，直接复制缓存的APK到目标位置
  4. 如无，执行反编译、修改、编译流程
  5. 保存修改后的APK到缓存
```

## 性能对比

| 场景 | 无缓存 | 有缓存 | 节省时间 |
|------|--------|--------|---------|
| 首次移植 | 15分钟 | 15分钟 | 0% |
| 同Port ROM换机型 | 15分钟 | 5-8分钟 | 50-70% |
| 仅修改配置重打包 | 15分钟 | 3-5分钟 | 70-80% |

## 缓存失效策略

缓存会在以下情况下自动失效：

1. **ROM文件变化**: 文件哈希不匹配
2. **缓存版本升级**: 缓存格式版本变化
3. **APK修改器版本变化**: `cache_version`属性变化
4. **完整性检查失败**: 文件数量或内容不匹配

## 注意事项

1. **缓存位置**: 默认存储在项目根目录的 `.cache/portroms/`，确保有足够磁盘空间
2. **并发安全**: 文件锁机制确保多进程安全，但同一ROM的并发修改仍可能冲突
3. **清理策略**: 目前需要手动清理缓存，未来将支持自动清理策略
4. **设备特定文件**: Vendor/ODM分区、Firmware镜像不会缓存（设备特定）

## 故障排除

### 缓存未命中

如果缓存一直未命中，检查：
1. `--no-cache` 参数是否被误用
2. ROM文件是否有变化（哈希不同）
3. 缓存目录权限是否正确

### 清理损坏的缓存

```bash
# 验证并自动修复
python tools/cache_manager.py verify --fix

# 或清除所有缓存重新开始
python tools/cache_manager.py clean --all
```

## 未来计划

- [ ] 自动缓存大小限制和LRU清理
- [ ] 缓存压缩以节省磁盘空间
- [ ] 分布式缓存支持（多机器共享）
- [ ] 缓存预加载和后台同步
