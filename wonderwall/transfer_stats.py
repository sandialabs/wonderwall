"""Transfer statistics tracking for HTTPS proxy connections."""

import asyncio
import logging

log = logging.getLogger(__name__)


class TransferStats:
    """Track bytes transferred and provide periodic logging for proxy connections.

    This class tracks data transfer in both directions (client→upstream and upstream→client)
    and provides periodic logging of transfer statistics including byte counts and transfer rates.

    Attributes:
        request_id (str): Unique identifier for the connection
        addr (tuple): Client address (host, port)
        hostname (str): Target hostname
        bytes_upstream (int): Bytes sent from client to upstream
        bytes_downstream (int): Bytes sent from upstream to client
        last_update_time (float): Last time statistics were logged
        last_update_bytes (int): Total bytes at last update
        update_interval (float): Minimum time between updates (seconds)
        update_threshold (int): Minimum bytes between updates
    """

    def __init__(self, request_id: str, addr: tuple, hostname: str):
        """Initialize transfer statistics tracker.

        Args:
            request_id: Unique identifier for this connection
            addr: Client address as (host, port) tuple
            hostname: Target hostname being connected to
        """
        self.request_id = request_id
        self.addr = addr
        self.hostname = hostname
        self.bytes_upstream = 0
        self.bytes_downstream = 0
        self.last_update_time = 0.0  # Start time will be set on first log_transfer_update call
        self.last_update_bytes = 0
        self.update_interval = 5.0  # seconds
        self.update_threshold = 1024 * 1024  # 1MB

    def log_transfer_update(self, force_logging=False) -> str | None:
        """Check if transfer update should be logged and return the log message.

        Logs are generated when either:
        - At least update_interval seconds have passed since last update
        - At least update_threshold bytes have been transferred since last update

        The log includes:
        - Total bytes transferred (upstream + downstream)
        - Bytes in each direction
        - Transfer rate since last update (KB/s)

        Args:
            force_logging: If True, always log an update. Used when the connection
            is being closed.

        Returns:
            Log message string if update should be logged, None otherwise
        """
        current_time = asyncio.get_event_loop().time()
        total_bytes = self.bytes_upstream + self.bytes_downstream
        bytes_since_last = total_bytes - self.last_update_bytes

        # Check if we should log an update
        # If this is the first call (last_update_time is 0), always log if we have data
        if self.last_update_time == 0.0:
            if total_bytes > 0:
                # First update - set initial time and log
                time_since_last = 0
                rate_kb_s = 0

                log_message = "[%s] %s → %s: transferred %d bytes (up: %d, down: %d), rate: %.2f KB/s" % (
                    self.request_id, self.addr, self.hostname, total_bytes,
                    self.bytes_upstream, self.bytes_downstream, rate_kb_s)

                log.info(log_message)

                # Set tracking for next interval
                self.last_update_time = current_time
                self.last_update_bytes = total_bytes

                return log_message
            else:
                return None

        # Normal case: check thresholds
        if force_logging or (current_time - self.last_update_time >= self.update_interval or
            bytes_since_last >= self.update_threshold):

            time_since_last = current_time - self.last_update_time
            if time_since_last > 0:
                rate_kb_s = bytes_since_last / time_since_last / 1024  # KB/s
            else:
                rate_kb_s = 0

            log_message = "[%s] %s → %s: transferred %d bytes (up: %d, down: %d), rate: %.2f KB/s" % (
                self.request_id, self.addr, self.hostname, total_bytes,
                self.bytes_upstream, self.bytes_downstream, rate_kb_s)

            log.info(log_message)

            # Reset tracking for next interval
            self.last_update_time = current_time
            self.last_update_bytes = total_bytes

            return log_message

        return None

    def reset(self):
        """Reset all transfer statistics.

        Useful when reusing a TransferStats instance for a new connection.
        """
        self.bytes_upstream = 0
        self.bytes_downstream = 0
        self.last_update_time = 0.0
        self.last_update_bytes = 0

    def get_total_bytes(self) -> int:
        """Get total bytes transferred in both directions.

        Returns:
            Total bytes transferred (upstream + downstream)
        """
        return self.bytes_upstream + self.bytes_downstream

    def get_upstream_bytes(self) -> int:
        """Get bytes transferred from client to upstream.

        Returns:
            Bytes sent from client to upstream server
        """
        return self.bytes_upstream

    def get_downstream_bytes(self) -> int:
        """Get bytes transferred from upstream to client.

        Returns:
            Bytes sent from upstream server to client
        """
        return self.bytes_downstream

    def get_transfer_ratio(self) -> float:
        """Get the ratio of upstream to downstream bytes.

        Returns:
            Ratio of upstream:downstream bytes, or 0 if no downstream bytes
        """
        if self.bytes_downstream == 0:
            return 0.0
        return self.bytes_upstream / self.bytes_downstream