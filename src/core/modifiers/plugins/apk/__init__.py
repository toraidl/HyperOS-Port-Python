"""APK modifier plugins package.

This package contains plugins for modifying specific APKs in the ROM.
Each plugin handles one APK and applies specific patches.
"""

from src.core.modifiers.plugins.apk.base import ApkModifierPlugin, ApkModifierRegistry
from src.core.modifiers.plugins.apk.installer import InstallerModifier
from src.core.modifiers.plugins.apk.securitycenter import SecurityCenterModifier
from src.core.modifiers.plugins.apk.settings import SettingsModifier
from src.core.modifiers.plugins.apk.joyose import JoyoseModifier
from src.core.modifiers.plugins.apk.powerkeeper import PowerKeeperModifier
from src.core.modifiers.plugins.apk.devices_overlay import DevicesOverlayModifier

__all__ = [
    'ApkModifierPlugin',
    'ApkModifierRegistry',
    'InstallerModifier',
    'SecurityCenterModifier',
    'SettingsModifier',
    'JoyoseModifier',
    'PowerKeeperModifier',
    'DevicesOverlayModifier',
]
