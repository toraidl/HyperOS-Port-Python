"""
Tests for PortRomCacheManager

Run with: pytest tests/core/test_cache_manager.py -v
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from src.core.cache_manager import PortRomCacheManager, FileLock, CacheMetadata


class TestPortRomCacheManager:
    """Test suite for PortRomCacheManager"""

    def test_init_creates_cache_root(self):
        """Test that initialization creates cache root directory"""
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp) / ".cache"
            cache = PortRomCacheManager(cache_root)
            assert cache.cache_root.exists()
            assert cache.cache_root == cache_root

    def test_compute_rom_hash_small_file(self):
        """Test ROM hash computation for small files"""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a small test file
            test_file = Path(tmp) / "test.zip"
            test_file.write_bytes(b"test content for hashing")

            cache = PortRomCacheManager(tmp)
            hash1 = cache._compute_rom_hash(test_file)
            hash2 = cache._compute_rom_hash(test_file)

            assert isinstance(hash1, str)
            assert len(hash1) == 32  # MD5 produces 32 char hex string
            assert hash1 == hash2  # Hash should be deterministic

    def test_is_partition_cached_false_when_empty(self):
        """Test that is_partition_cached returns False for non-existent cache"""
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "test.zip"
            test_file.write_bytes(b"test")

            cache = PortRomCacheManager(tmp)
            assert not cache.is_partition_cached(test_file, "system")

    def test_store_and_restore_partition(self):
        """Test storing and restoring a partition"""
        with tempfile.TemporaryDirectory() as tmp:
            # Create test ROM file
            test_rom = Path(tmp) / "port.zip"
            test_rom.write_bytes(b"test rom content")

            # Create source directory with some files
            source_dir = Path(tmp) / "source_system"
            source_dir.mkdir()
            (source_dir / "file1.txt").write_text("content1")
            (source_dir / "subdir").mkdir()
            (source_dir / "subdir" / "file2.txt").write_text("content2")

            cache = PortRomCacheManager(tmp)

            # Store partition
            result = cache.store_partition(test_rom, "system", source_dir)
            assert result is True

            # Check that partition is now cached
            assert cache.is_partition_cached(test_rom, "system")

            # Restore partition
            target_dir = Path(tmp) / "target_system"
            result = cache.restore_partition(test_rom, "system", target_dir)
            assert result is True

            # Verify restored content
            assert (target_dir / "file1.txt").read_text() == "content1"
            assert (target_dir / "subdir" / "file2.txt").read_text() == "content2"

    def test_clear_partition(self):
        """Test clearing a specific partition cache"""
        with tempfile.TemporaryDirectory() as tmp:
            test_rom = Path(tmp) / "port.zip"
            test_rom.write_bytes(b"test")
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()

            cache = PortRomCacheManager(tmp)
            cache.store_partition(test_rom, "system", source_dir)

            assert cache.is_partition_cached(test_rom, "system")

            cache.clear_partition(test_rom, "system")

            assert not cache.is_partition_cached(test_rom, "system")

    def test_clear_all(self):
        """Test clearing all cache"""
        with tempfile.TemporaryDirectory() as tmp:
            test_rom = Path(tmp) / "port.zip"
            test_rom.write_bytes(b"test")
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()

            cache = PortRomCacheManager(tmp)
            cache.store_partition(test_rom, "system", source_dir)
            cache.store_partition(test_rom, "product", source_dir)

            assert cache.is_partition_cached(test_rom, "system")
            assert cache.is_partition_cached(test_rom, "product")

            cache.clear_all()

            assert not cache.is_partition_cached(test_rom, "system")
            assert not cache.is_partition_cached(test_rom, "product")

    def test_get_cache_info(self):
        """Test getting cache statistics"""
        with tempfile.TemporaryDirectory() as tmp:
            test_rom = Path(tmp) / "port.zip"
            test_rom.write_bytes(b"test")
            source_dir = Path(tmp) / "source"
            source_dir.mkdir()
            (source_dir / "file.txt").write_text("content")

            cache = PortRomCacheManager(tmp)
            cache.store_partition(test_rom, "system", source_dir)

            info = cache.get_cache_info()

            assert "version" in info
            assert "cache_root" in info
            assert "total_size_bytes" in info
            assert "cached_roms" in info
            assert len(info["cached_roms"]) >= 1


class TestFileLock:
    """Test suite for FileLock"""

    def test_file_lock_acquire_and_release(self):
        """Test basic file lock acquire and release"""
        with tempfile.TemporaryDirectory() as tmp:
            lock_file = Path(tmp) / "test.lock"

            lock = FileLock(lock_file)
            assert lock.acquire() is True
            lock.release()

    def test_file_lock_context_manager(self):
        """Test file lock as context manager"""
        with tempfile.TemporaryDirectory() as tmp:
            lock_file = Path(tmp) / "test.lock"

            with FileLock(lock_file) as lock:
                assert lock._lock_fd is not None

            assert lock._lock_fd is None

    def test_file_lock_timeout(self):
        """Test file lock timeout"""
        import threading
        import time

        with tempfile.TemporaryDirectory() as tmp:
            lock_file = Path(tmp) / "test.lock"

            # Acquire lock in another thread
            lock_acquired = threading.Event()

            def hold_lock():
                with FileLock(lock_file):
                    lock_acquired.set()
                    time.sleep(1)  # Hold for 1 second

            thread = threading.Thread(target=hold_lock)
            thread.start()
            lock_acquired.wait()  # Wait for lock to be acquired

            # Try to acquire with short timeout
            with pytest.raises(TimeoutError):
                with FileLock(lock_file, timeout=0.1):
                    pass

            thread.join()


class TestCacheMetadata:
    """Test suite for CacheMetadata"""

    def test_metadata_to_dict(self):
        """Test converting metadata to dict"""
        metadata = CacheMetadata(rom_hash="abc123", partition_name="system", file_count=100)

        data = metadata.to_dict()
        assert data["rom_hash"] == "abc123"
        assert data["partition_name"] == "system"
        assert data["file_count"] == 100

    def test_metadata_from_dict(self):
        """Test creating metadata from dict"""
        data = {
            "version": "1.0",
            "rom_hash": "abc123",
            "partition_name": "system",
            "file_count": 100,
            "total_size": 1000000,
        }

        metadata = CacheMetadata.from_dict(data)
        assert metadata.rom_hash == "abc123"
        assert metadata.partition_name == "system"
        assert metadata.file_count == 100
