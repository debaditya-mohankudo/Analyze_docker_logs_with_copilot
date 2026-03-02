"""
Buffer Manager - Time-windowed in-memory log storage.
Maintains circular buffers per container for fast context retrieval.

Performance optimizations:
- Uses @dataclass(slots=True) for ~30% memory reduction per entry
- Eliminates separate timestamp tracking (single LogEntry objects)
- Simple linear scan for time windows (faster than bisect for small buffers)
- Lock contention minimized with container-level buffers
"""

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Tuple, Optional

import config
from logger import logger


@dataclass(slots=True)
class LogEntry:
    """Lightweight log entry with timestamp and payload."""
    ts: float
    payload: str


class TimeWindowBuffer:
    """High-performance circular buffer with time-based windowing."""
    
    def __init__(self, container_name: str, maxlen: int = None):
        self.container_name = container_name
        self.maxlen = maxlen or config.BUFFER_SIZE_PER_CONTAINER
        self.buffer: Deque[LogEntry] = deque(maxlen=self.maxlen)
        self.lock = threading.Lock()
        
    def add_log(self, timestamp: float, log_line: str):
        """Add a log entry to the buffer (thread-safe)."""
        with self.lock:
            self.buffer.append(LogEntry(timestamp, log_line))
    
    def get_window(self, start_time: float, end_time: float) -> List[Tuple[float, str]]:
        """
        Retrieve logs within the time window [start_time, end_time].
        Returns list of (timestamp, log_line) tuples.
        """
        with self.lock:
            if not self.buffer:
                return []
            
            # Filter entries within time window
            results = [
                (entry.ts, entry.payload)
                for entry in self.buffer
                if start_time <= entry.ts <= end_time
            ]
            
            return results
    
    def cleanup_old_logs(self, retention_seconds: int = 180):
        """Remove logs older than retention_seconds."""
        with self.lock:
            if not self.buffer:
                return
            
            cutoff_time = time.time() - retention_seconds
            removed = 0
            
            # Evict old entries from left
            while self.buffer and self.buffer[0].ts < cutoff_time:
                self.buffer.popleft()
                removed += 1
            
            if removed > 0:
                logger.debug(f"Cleaned up {removed} old logs from {self.container_name}")
    
    def size(self) -> int:
        """Return current buffer size."""
        with self.lock:
            return len(self.buffer)
    
    def get_time_range(self) -> Optional[Tuple[float, float]]:
        """Return (oldest_timestamp, newest_timestamp) or None if empty."""
        with self.lock:
            if not self.buffer:
                return None
            return (self.buffer[0].ts, self.buffer[-1].ts)


class BufferManager:
    """Manages time-window buffers for all containers."""
    
    def __init__(self):
        self._buffers: Dict[str, TimeWindowBuffer] = {}
        self._lock = threading.Lock()
        logger.info("BufferManager initialized")
    
    def get_or_create_buffer(self, container_name: str) -> TimeWindowBuffer:
        """Get existing buffer or create new one for container."""
        with self._lock:
            if container_name not in self._buffers:
                self._buffers[container_name] = TimeWindowBuffer(container_name)
                logger.info(f"Created buffer for container: {container_name}")
            return self._buffers[container_name]
    
    def add_log(self, container_name: str, timestamp: float, log_line: str):
        """Add log entry to container's buffer."""
        buffer = self.get_or_create_buffer(container_name)
        buffer.add_log(timestamp, log_line)
    
    def get_all_windows(self, start_time: float, end_time: float) -> Dict[str, List[Tuple[float, str]]]:
        """
        Retrieve logs from all containers within the time window.
        Returns dict: {container_name: [(timestamp, log_line), ...]}
        """
        results = {}
        with self._lock:
            for container_name, buffer in self._buffers.items():
                logs = buffer.get_window(start_time, end_time)
                if logs:  # Only include containers with logs in window
                    results[container_name] = logs
        
        return results
    
    def cleanup_all(self, retention_seconds: int = 180):
        """Cleanup old logs from all buffers."""
        with self._lock:
            for buffer in self._buffers.values():
                buffer.cleanup_old_logs(retention_seconds)
    
    def get_stats(self) -> Dict[str, Dict]:
        """Get buffer statistics for monitoring."""
        stats = {}
        with self._lock:
            for container_name, buffer in self._buffers.items():
                time_range = buffer.get_time_range()
                stats[container_name] = {
                    'size': buffer.size(),
                    'oldest': time_range[0] if time_range else None,
                    'newest': time_range[1] if time_range else None,
                }
        return stats
    
    def remove_container(self, container_name: str):
        """Remove buffer for a stopped container."""
        with self._lock:
            if container_name in self._buffers:
                del self._buffers[container_name]
                logger.info(f"Removed buffer for container: {container_name}")


if __name__ == '__main__':
    # Test the buffer manager
    manager = BufferManager()
    
    # Add test logs
    current_time = time.time()
    manager.add_log("test-container", current_time - 120, "Log from 2 minutes ago")
    manager.add_log("test-container", current_time - 60, "Log from 1 minute ago")
    manager.add_log("test-container", current_time, "Current log")
    
    # Test window retrieval
    window = manager.get_all_windows(current_time - 90, current_time + 10)
    print(f"Logs in window: {window}")
    
    # Test stats
    print(f"Stats: {manager.get_stats()}")
