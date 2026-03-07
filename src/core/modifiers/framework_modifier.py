"""Framework-level modifications (smali patching).

.. deprecated::
    This module has been refactored. The FrameworkModifier class is now
    located in ``src.core.modifiers.framework``. This module is kept for
    backward compatibility and will be removed in a future version.

    Please update your imports:

    Before:
        from src.core.modifiers.framework_modifier import FrameworkModifier

    After:
        from src.core.modifiers.framework import FrameworkModifier
"""

from __future__ import annotations

import warnings

# Re-export all components from the new location for backward compatibility
from src.core.modifiers.framework import (
    FrameworkModifier,
    FrameworkModifierBase,
    FrameworkTasks,
    RETRUN_TRUE,
    RETRUN_FALSE,
    REMAKE_VOID,
    INVOKE_TRUE,
    PRELOADS_SHAREDUIDS,
    MY_PLATFORM_KEY,
)

# Emit deprecation warning
warnings.warn(
    "src.core.modifiers.framework_modifier is deprecated. "
    "Use src.core.modifiers.framework instead.",
    DeprecationWarning,
    stacklevel=2,
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
