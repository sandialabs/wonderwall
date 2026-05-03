"""Comprehensive tests for TransferStats class."""

import sys
from unittest.mock import patch
import pytest

# Add project to path for direct imports
sys.path.insert(0, '.')

from wonderwall.transfer_stats import TransferStats


class TestTransferStatsInitialization:
    """Test TransferStats initialization and basic properties."""

    def test_initialization_with_valid_parameters(self):
        """Test that TransferStats initializes correctly with valid parameters."""
        stats = TransferStats("test1234", ("127.0.0.1", 12345), "example.com")

        assert stats.request_id == "test1234"
        assert stats.addr == ("127.0.0.1", 12345)
        assert stats.hostname == "example.com"
        assert stats.bytes_upstream == 0
        assert stats.bytes_downstream == 0
        assert stats.last_update_bytes == 0
        assert stats.update_interval == 5.0
        assert stats.update_threshold == 1024 * 1024

    def test_initialization_with_different_address_formats(self):
        """Test initialization with various address formats."""
        # IPv4
        stats1 = TransferStats("id1", ("192.168.1.1", 8080), "localhost")
        assert stats1.addr == ("192.168.1.1", 8080)

        # IPv6
        stats2 = TransferStats("id2", ("::1", 443), "ipv6.example.com")
        assert stats2.addr == ("::1", 443)

        # Hostname
        stats3 = TransferStats("id3", ("localhost", 80), "example.com")
        assert stats3.addr == ("localhost", 80)

    def test_initialization_with_empty_hostname(self):
        """Test initialization with empty hostname."""
        stats = TransferStats("id4", ("127.0.0.1", 12345), "")
        assert stats.hostname == ""


class TestTransferStatsByteTracking:
    """Test byte tracking functionality."""

    def test_initial_byte_counts(self):
        """Test that initial byte counts are zero."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        assert stats.get_total_bytes() == 0
        assert stats.get_upstream_bytes() == 0
        assert stats.get_downstream_bytes() == 0

    def test_upstream_byte_tracking(self):
        """Test tracking of upstream bytes."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        stats.bytes_upstream = 1024
        assert stats.get_upstream_bytes() == 1024
        assert stats.get_total_bytes() == 1024

    def test_downstream_byte_tracking(self):
        """Test tracking of downstream bytes."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        stats.bytes_downstream = 2048
        assert stats.get_downstream_bytes() == 2048
        assert stats.get_total_bytes() == 2048

    def test_bidirectional_byte_tracking(self):
        """Test tracking of bytes in both directions."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        stats.bytes_upstream = 512000  # 500KB
        stats.bytes_downstream = 1024000  # 1MB

        assert stats.get_upstream_bytes() == 512000
        assert stats.get_downstream_bytes() == 1024000
        assert stats.get_total_bytes() == 1536000

    def test_transfer_ratio_calculation(self):
        """Test transfer ratio calculation."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        # Equal transfer
        stats.bytes_upstream = 1000
        stats.bytes_downstream = 1000
        assert stats.get_transfer_ratio() == 1.0

        # More upstream
        stats.bytes_upstream = 2000
        stats.bytes_downstream = 1000
        assert stats.get_transfer_ratio() == 2.0

        # More downstream
        stats.bytes_upstream = 500
        stats.bytes_downstream = 2000
        assert stats.get_transfer_ratio() == 0.25

        # Zero downstream (edge case)
        stats.bytes_upstream = 100
        stats.bytes_downstream = 0
        assert stats.get_transfer_ratio() == 0.0


class TestTransferStatsLogging:
    """Test transfer logging functionality."""

    def test_log_transfer_update_below_thresholds(self):
        """Test that logging doesn't occur when thresholds aren't met after first call."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        with patch('asyncio.get_event_loop') as mock_loop:
            # First call - should log (first call with data)
            mock_loop.return_value.time.return_value = 1.0
            stats.bytes_upstream = 1000
            stats.bytes_downstream = 2000
            result1 = stats.log_transfer_update()
            assert result1 is not None  # First call logs

            # Second call - should not log (below thresholds)
            mock_loop.return_value.time.return_value = 2.0  # Only 1 second later
            stats.bytes_upstream = 1500  # Small increase
            stats.bytes_downstream = 2500  # Small increase
            result2 = stats.log_transfer_update()
            assert result2 is None  # Below thresholds, no log

    def test_log_transfer_update_time_threshold(self):
        """Test that logging occurs when time threshold is met."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        # Small transfer but long time
        stats.bytes_upstream = 1000
        stats.bytes_downstream = 2000

        with patch('asyncio.get_event_loop') as mock_loop:
            mock_loop.return_value.time.return_value = 6.0  # 6 seconds later (over 5s threshold)

            # Should return log message
            result = stats.log_transfer_update()
            assert result is not None
            assert "test" in result  # request_id
            assert "127.0.0.1" in result  # addr
            assert "example.com" in result  # hostname
            assert "transferred 3000 bytes" in result
            assert "up: 1000, down: 2000" in result
            assert "rate:" in result

    def test_log_transfer_update_byte_threshold(self):
        """Test that logging occurs when byte threshold is met."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        # Large transfer in short time
        stats.bytes_upstream = 600000  # 600KB
        stats.bytes_downstream = 600000  # 600KB (total 1.2MB, over 1MB threshold)

        with patch('asyncio.get_event_loop') as mock_loop:
            mock_loop.return_value.time.return_value = 1.0  # Only 1 second later

            # Should return log message
            result = stats.log_transfer_update()
            assert result is not None
            assert "transferred 1200000 bytes" in result

    def test_log_transfer_update_multiple_calls(self):
        """Test multiple calls to log_transfer_update."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        with patch('asyncio.get_event_loop') as mock_loop:
            # First call - should log (first call with data)
            mock_loop.return_value.time.return_value = 1.0
            stats.bytes_upstream = 1000
            stats.bytes_downstream = 2000
            result1 = stats.log_transfer_update()
            assert result1 is not None  # First call logs

            # Second call - should not log (below thresholds)
            mock_loop.return_value.time.return_value = 2.0
            result2 = stats.log_transfer_update()
            assert result2 is None

            # Third call - should log (over time threshold)
            mock_loop.return_value.time.return_value = 7.0  # 5 seconds after last update
            result3 = stats.log_transfer_update()
            assert result3 is not None

            # Fourth call - should not log immediately after previous log
            mock_loop.return_value.time.return_value = 7.1
            result4 = stats.log_transfer_update()
            assert result4 is None


class TestTransferStatsReset:
    """Test reset functionality."""

    def test_reset_clears_all_stats(self):
        """Test that reset clears all transfer statistics."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        # Set some values
        stats.bytes_upstream = 1000
        stats.bytes_downstream = 2000
        stats.last_update_time = 5.0
        stats.last_update_bytes = 1500

        # Reset
        stats.reset()

        # Verify all values are reset
        assert stats.bytes_upstream == 0
        assert stats.bytes_downstream == 0
        assert stats.last_update_bytes == 0
        # last_update_time should be set to current time (mocked)
        assert stats.last_update_time == 0.0  # Default from get_event_loop().time()

    def test_reset_preserves_configuration(self):
        """Test that reset preserves configuration but clears data."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        # Modify configuration
        stats.update_interval = 10.0
        stats.update_threshold = 2048

        # Set some data
        stats.bytes_upstream = 1000

        # Reset
        stats.reset()

        # Configuration should be preserved
        assert stats.update_interval == 10.0
        assert stats.update_threshold == 2048

        # Data should be cleared
        assert stats.bytes_upstream == 0


class TestTransferStatsEdgeCases:
    """Test edge cases and error conditions."""

    def test_zero_time_elapsed(self):
        """Test behavior when no time has elapsed."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        stats.bytes_upstream = 1000
        stats.bytes_downstream = 2000

        with patch('asyncio.get_event_loop') as mock_loop:
            mock_loop.return_value.time.return_value = 0.0  # No time elapsed

            # Should log (byte threshold met) but rate should be 0
            result = stats.log_transfer_update()
            assert result is not None
            assert "rate: 0.00 KB/s" in result

    def test_force_logging_parameter(self):
        """Test that force_logging=True always logs regardless of thresholds."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        # Set up initial state
        stats.bytes_upstream = 100
        stats.bytes_downstream = 200

        with patch('asyncio.get_event_loop') as mock_loop:
            mock_loop.return_value.time.return_value = 1.0

            # First call - should log normally
            result1 = stats.log_transfer_update()
            assert result1 is not None

            # Second call - below thresholds, should NOT log normally
            mock_loop.return_value.time.return_value = 1.1  # Only 0.1 seconds later
            stats.bytes_upstream = 150  # Small increase
            stats.bytes_downstream = 250  # Small increase
            result2 = stats.log_transfer_update()
            assert result2 is None  # Below thresholds, no log

            # Third call - same conditions but with force_logging=True, SHOULD log
            result3 = stats.log_transfer_update(force_logging=True)
            assert result3 is not None
            assert "test" in result3  # request_id
            assert "127.0.0.1" in result3  # addr
            assert "example.com" in result3  # hostname
            assert "transferred 400 bytes" in result3
            assert "up: 150, down: 250" in result3

            # Fourth call - force_logging=True should work even with no new data
            result4 = stats.log_transfer_update(force_logging=True)
            assert result4 is not None
            # Should show same byte counts since no new data
            assert "transferred 400 bytes" in result4
            assert "up: 150, down: 250" in result4

    def test_negative_byte_counts(self):
        """Test behavior with negative byte counts (shouldn't happen but test anyway)."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        stats.bytes_upstream = -100
        stats.bytes_downstream = -200

        assert stats.get_total_bytes() == -300
        assert stats.get_upstream_bytes() == -100
        assert stats.get_downstream_bytes() == -200

    def test_very_large_byte_counts(self):
        """Test behavior with very large byte counts."""
        stats = TransferStats("test", ("127.0.0.1", 12345), "example.com")

        stats.bytes_upstream = 10 ** 9  # 1GB
        stats.bytes_downstream = 2 * 10 ** 9  # 2GB

        assert stats.get_total_bytes() == 3 * 10 ** 9
        assert stats.get_transfer_ratio() == 0.5


class TestTransferStatsIntegration:
    """Integration tests for realistic usage patterns."""

    def test_simulated_http_request(self):
        """Simulate a typical HTTP request/response cycle."""
        stats = TransferStats("req123", ("192.168.1.100", 54321), "api.example.com")

        # Simulate HTTP request (small upstream)
        stats.bytes_upstream = 512  # Small request

        # Simulate HTTP response (larger downstream)
        stats.bytes_downstream = 4096  # Larger response

        assert stats.get_total_bytes() == 4608
        assert stats.get_transfer_ratio() == 512 / 4096 == 0.125

    def test_simulated_file_download(self):
        """Simulate a file download scenario."""
        stats = TransferStats("dl456", ("192.168.1.100", 54321), "cdn.example.com")

        # Small request
        stats.bytes_upstream = 256

        # Large download
        stats.bytes_downstream = 10 * 1024 * 1024  # 10MB

        assert stats.get_total_bytes() == 10 * 1024 * 1024 + 256
        assert stats.get_transfer_ratio() < 0.0001  # Very small ratio

    def test_simulated_file_upload(self):
        """Simulate a file upload scenario."""
        stats = TransferStats("ul789", ("192.168.1.100", 54321), "upload.example.com")

        # Large upload
        stats.bytes_upstream = 5 * 1024 * 1024  # 5MB

        # Small response
        stats.bytes_downstream = 128

        assert stats.get_total_bytes() == 5 * 1024 * 1024 + 128
        assert stats.get_transfer_ratio() > 1000  # Very large ratio


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "-s"])