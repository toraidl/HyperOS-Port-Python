"""ROM Modifiers - Modular modification system for ROM porting.

This package provides a modular approach to ROM modification:
- SystemModifier: System-level modifications (wild_boost, replacements, features)
- FrameworkModifier: Framework-level smali patching
- FirmwareModifier: Firmware modifications (vbmeta, KernelSU)
- RomModifier: Overall coordination of modification phases
- ApkModifier: APK-level modifications (installer, settings, etc.)
- UnifiedModifier: Combines all modification types
- Plugin system for extensible modifications
"""

# Core modifier classes
from src.core.modifiers.base_modifier import BaseModifier
from src.core.modifiers.firmware_modifier import FirmwareModifier
from src.core.modifiers.framework_modifier import FrameworkModifier
from src.core.modifiers.rom_modifier import RomModifier
from src.core.modifiers.smali_args import SmaliArgs
from src.core.modifiers.system_modifier import SystemModifier
from src.core.modifiers.unified_modifier import ApkModifier, UnifiedModifier

# Plugin system
from src.core.modifiers.plugin_system import (
    ModifierPlugin,
    ModifierRegistry,
    PluginConfig,
    PluginManager,
    create_backup_hook,
    create_backup_hook_factory,
    load_plugins_from_config,
)

# Transaction system
from src.core.modifiers.transaction import (
    ModificationRecord,
    RollbackContext,
    Transaction,
    TransactionManager,
)

# Built-in system plugins
from src.core.modifiers.plugins import (
    EULocalizationPlugin,
    FeatureUnlockPlugin,
    FileReplacementPlugin,
    VNDKFixPlugin,
    WildBoostPlugin,
)

# APK plugins
from src.core.modifiers.plugins.apk import (
    ApkModifierPlugin,
    ApkModifierRegistry,
    DevicesOverlayModifier,
    InstallerModifier,
    JoyoseModifier,
    PowerKeeperModifier,
    SecurityCenterModifier,
    SettingsModifier,
)

__all__ = [
    # Core modifiers
    "BaseModifier",
    "SmaliArgs",
    "SystemModifier",
    "FrameworkModifier",
    "FirmwareModifier",
    "RomModifier",
    # Unified modifier
    "UnifiedModifier",
    "ApkModifier",
    # Plugin system
    "ModifierPlugin",
    "PluginManager",
    "ModifierRegistry",
    "create_backup_hook",
    "create_backup_hook_factory",
    "load_plugins_from_config",
    "PluginConfig",
    # Transaction system
    "TransactionManager",
    "Transaction",
    "ModificationRecord",
    "RollbackContext",
    # System plugins
    "WildBoostPlugin",
    "EULocalizationPlugin",
    "FeatureUnlockPlugin",
    "VNDKFixPlugin",
    "FileReplacementPlugin",
    # APK plugins
    "ApkModifierPlugin",
    "ApkModifierRegistry",
    "InstallerModifier",
    "SecurityCenterModifier",
    "SettingsModifier",
    "JoyoseModifier",
    "PowerKeeperModifier",
    "DevicesOverlayModifier",
]
