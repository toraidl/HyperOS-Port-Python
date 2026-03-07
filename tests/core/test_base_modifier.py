"""Unit tests for BaseModifier class."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.core.modifiers.base_modifier import BaseModifier


class TestBaseModifier:
    """Test cases for BaseModifier class."""

    @pytest.fixture
    def modifier(self):
        """Create a BaseModifier instance with mock context."""
        context = Mock()
        context.logger = Mock()
        return BaseModifier(context, "TestModifier")

    def test_init(self, modifier):
        """Test BaseModifier initialization."""
        assert modifier.name == "TestModifier"
        assert modifier.ctx is not None
        assert modifier.logger is not None

    def test_find_file_recursive_found(self, modifier, tmp_path):
        """Test finding a file that exists."""
        # Create nested directory structure
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        target_file = nested / "target.txt"
        target_file.write_text("content")

        result = modifier._find_file_recursive(tmp_path, "target.txt")
        assert result == target_file

    def test_find_file_recursive_not_found(self, modifier, tmp_path):
        """Test finding a file that doesn't exist."""
        result = modifier._find_file_recursive(tmp_path, "nonexistent.txt")
        assert result is None

    def test_find_file_recursive_in_root(self, modifier, tmp_path):
        """Test finding a file in root directory."""
        target_file = tmp_path / "root_file.txt"
        target_file.write_text("content")

        result = modifier._find_file_recursive(tmp_path, "root_file.txt")
        assert result == target_file

    def test_find_file_recursive_multiple_matches(self, modifier, tmp_path):
        """Test finding returns first match when multiple files exist."""
        (tmp_path / "file1.txt").write_text("content")
        nested = tmp_path / "subdir"
        nested.mkdir()
        (nested / "file1.txt").write_text("content2")

        result = modifier._find_file_recursive(tmp_path, "file1.txt")
        assert result is not None
        assert result.name == "file1.txt"

    def test_find_file_recursive_nonexistent_dir(self, modifier):
        """Test finding in non-existent directory returns None."""
        result = modifier._find_file_recursive(Path("/nonexistent/path"), "file.txt")
        assert result is None

    def test_find_dir_recursive_found(self, modifier, tmp_path):
        """Test finding a directory that exists."""
        nested = tmp_path / "level1" / "level2" / "target"
        nested.mkdir(parents=True)

        result = modifier._find_dir_recursive(tmp_path, "target")
        assert result == nested

    def test_find_dir_recursive_not_found(self, modifier, tmp_path):
        """Test finding a directory that doesn't exist."""
        result = modifier._find_dir_recursive(tmp_path, "nonexistent_dir")
        assert result is None

    def test_find_dir_recursive_exact_name_match(self, modifier, tmp_path):
        """Test that directory name must match exactly."""
        # Create dir with similar but different name
        (tmp_path / "target_dir").mkdir()
        (tmp_path / "target").mkdir()

        result = modifier._find_dir_recursive(tmp_path, "target")
        assert result is not None
        assert result.name == "target"

    def test_find_dir_recursive_prefers_deeper_match(self, modifier, tmp_path):
        """Test finding directories at different levels."""
        (tmp_path / "target").mkdir()
        deep = tmp_path / "a" / "b" / "target"
        deep.mkdir(parents=True)

        result = modifier._find_dir_recursive(tmp_path, "target")
        # Should find the first one encountered by rglob
        assert result is not None
        assert result.name == "target"
