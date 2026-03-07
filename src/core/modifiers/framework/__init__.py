"""Framework modifier package for smali patching operations.

This package provides the FrameworkModifier class and supporting components
for modifying Android framework JARs including signature bypasses, PIF injection,
and EU ROM compatibility patches.

Example:
    >>> from src.core.modifiers.framework import FrameworkModifier
    >>> modifier = FrameworkModifier(context)
    >>> modifier.run()
"""

from __future__ import annotations

from src.core.modifiers.framework.modifier import FrameworkModifier
from src.core.modifiers.framework.base import FrameworkModifierBase
from src.core.modifiers.framework.tasks import FrameworkTasks
from src.core.modifiers.framework.patches import (
    RETRUN_TRUE,
    RETRUN_FALSE,
    REMAKE_VOID,
    INVOKE_TRUE,
    PRELOADS_SHAREDUIDS,
    MY_PLATFORM_KEY,
)

__all__ = [
    "FrameworkModifier",
    "FrameworkModifierBase",
    "FrameworkTasks",
    "RETRUN_TRUE",
    "RETRUN_FALSE",
    "REMAKE_VOID",
    "INVOKE_TRUE",
    "PRELOADS_SHAREDUIDS",
    "MY_PLATFORM_KEY",
]
