from __future__ import annotations

from enum import Enum, auto
from typing import List


ANDROID_LOGICAL_PARTITIONS: List[str] = [
    "system",
    "system_ext",
    "product",
    "vendor",
    "odm",
    "mi_ext",
    "system_dlkm",
    "vendor_dlkm",
    "odm_dlkm",
    "product_dlkm",
]


class RomType(Enum):
    """ROM package type enumeration."""

    UNKNOWN = auto()
    PAYLOAD = auto()  # payload.bin
    BROTLI = auto()  # new.dat.br
    FASTBOOT = auto()  # super.img or tgz
    LOCAL_DIR = auto()  # Pre-extracted directory
