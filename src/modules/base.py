import logging
from pathlib import Path
from src.utils.smalikit import SmaliKit, SmaliArgs
from src.utils.xml_utils import XmlUtils

class BaseModule:
    def __init__(self, run_smalikit_func, context):
        """
        :param run_smalikit_func: Smali modification function from core layer
        :param context: Global context (contains is_port_eu_rom, etc.)
        """
        self.run_smali = run_smalikit_func
        self.xml = self.xml = XmlUtils()
        self.ctx = context
        self.logger = logging.getLogger(self.__class__.__name__)

    def run(self, work_dir: Path):
        """Subclasses must implement this method"""
        raise NotImplementedError

    # Encapsulate common SmaliKit calls to simplify subclass code
    def smali_patch(self, work_dir, **kwargs):
        self.run_smali(path=str(work_dir), **kwargs)