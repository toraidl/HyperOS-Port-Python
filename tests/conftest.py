import logging
from unittest.mock import MagicMock

import pytest

from src.core.context import PortingContext


@pytest.fixture
def mock_context():
    """Returns a mock PortingContext."""
    mock = MagicMock(spec=PortingContext)
    mock.is_eu_port = False  # Default to CN port
    mock.logger = logging.getLogger("MockContext")
    return mock
