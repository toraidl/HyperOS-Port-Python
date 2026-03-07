"""Framework-level modifications (smali patching)."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.modifiers.framework.tasks import FrameworkTasks
from src.core.modifiers.framework.patches import (
    RETRUN_TRUE,
    RETRUN_FALSE,
    REMAKE_VOID,
    INVOKE_TRUE,
    PRELOADS_SHAREDUIDS,
)

if TYPE_CHECKING:
    from src.core.context import PortingContext


class FrameworkModifier(FrameworkTasks):
    """Handles framework-level modifications (smali patching).

    This class orchestrates the modification of framework JARs including:
    - miui-services.jar modifications for EU ROM compatibility
    - services.jar modifications for signature verification bypass
    - framework.jar modifications for PropsHook, PIF injection, and signature bypass
    - Xiaomi.eu Toolbox injection
    """

    def __init__(self, context: PortingContext) -> None:
        super().__init__(context)
        # Re-export patches as instance attributes for backward compatibility
        self.RETRUN_TRUE = RETRUN_TRUE
        self.RETRUN_FALSE = RETRUN_FALSE
        self.REMAKE_VOID = REMAKE_VOID
        self.INVOKE_TRUE = INVOKE_TRUE
        self.PRELOADS_SHAREDUIDS = PRELOADS_SHAREDUIDS

    def run(self) -> None:
        """Execute all framework modifications."""
        self.logger.info("Starting Framework Modification...")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            futures.append(executor.submit(self._mod_miui_services))
            futures.append(executor.submit(self._mod_services))
            futures.append(executor.submit(self._mod_framework))

            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"Framework modification failed: {e}")

        self._inject_xeu_toolbox()
        self.logger.info("Framework Modification Completed.")
