"""Base modifier class with common utilities."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.context import PortingContext


class BaseModifier:
    """Base class for all modifiers with common utilities."""

    def __init__(self, context: PortingContext, name: str) -> None:
        self.ctx: PortingContext = context
        self.name: str = name
        self.logger: logging.Logger = logging.getLogger(name)

    def _find_file_recursive(self, root_dir: Path, filename: str) -> Path | None:
        """Find a file recursively in a directory."""
        if not root_dir.exists():
            return None
        try:
            return next(root_dir.rglob(filename))
        except StopIteration:
            return None

    def _find_dir_recursive(self, root_dir: Path, dirname: str) -> Path | None:
        """Find a directory recursively in a directory."""
        if not root_dir.exists():
            return None
        for p in root_dir.rglob(dirname):
            if p.is_dir() and p.name == dirname:
                return p
        return None
