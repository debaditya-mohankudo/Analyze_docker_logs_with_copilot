"""
Buffer Manager - Time-windowed in-memory log storage.
Maintains circular buffers per container for fast context retrieval.
"""

import threading
import time
from bisect import bisect_left, bisect_right
from collections import deque
from typing import List, Tuple, Dict, Optional

import config
from logger import logger


class TimeWindowBuffer:
    """Thread-safe circular buffer with time-based windowing."""
    
    def __init__(self, container_name: str, maxlen: int = None):
        self.container_name = container_name
        self.maxlen = maxlen or config.BUFFER_SIZE_PER_CONTAINER
        self.buffer = deque(maxlen=self.maxlen)
        self.timestamps = deque(maxlen=self.maxlen)
        self.lock = threading.Lock()
        
    def add_log(self, timestamp: float, log_line: str):
        """Add a log entry to the buffer (thread-safe)."""
        with self.lock:
            self.buffer.append(log_line)
            self.timestamps.append(timestamp)
    
    def get_window(self, start_time: float, end_time: float) -> List[Tuple[float, str]]:
        """
        Retrieve logs within the time window [start_time, end_time].
        Returns list of (timestamp, log_line) tuples.
        """
        with self.lock:
            if not self.timestamps:
                return []
            
            # Convert deque to list for bisect operations
            ts_list = list(self.timestamps)
            
            # Find indices using binary search
            start_idx = bisect_left(ts_list, start_time)
            end_idx = bisect_right(ts_list, end_time)
            
            # Extract logs in the window
            results = []
            buffer_list = list(self.buffer)
            for i in range(start_idx, end_idx):
                results.append((ts_list[i], buffer_list[i]))
            
            return results
    
    def cleanup_old_logs(self, retention_seconds: int = 180):
        """Remove logs older than retention_seconds."""
        with self.lock:
            if not self.timestamps:
                return
            
            cutoff_time = time.time() - retention_seconds
            ts_list = list(self.timestamps)
            
            # Find first index to keep
            keep_idx = bisect_left(ts_list, cutoff_time)
            
            if keep_idx > 0:
                # Remove old entries
                for _ in range(keep_idx):
                    self.buffer.popleft()
                    self.timestamps.popleft()
                
                logger.debug(f"Cleaned up {keep_idx} old logs from {self.container_name}")
    
    def size(self) -> int:
        """Return current buffer size."""
        with self.lock:
            return len(self.buffer)
    
    def get_time_range(self) -> Optional[Tuple[float, float]]:
        """Return (oldest_timestamp, newest_timestamp) or None if empty."""
        with self.lock:
            if not self.timestamps:
                return None
            return (self.timestamps[0], self.timestamps[-1])


class BufferManager:
    """Manages time-window buffers for all containers."""
    
    def __init__(self):
        self.buffers: Dict[str, TimeWindowBuffer] = {}
        self.lock = threading.Lock()
        logger.info("BufferManager initialized")
    
    def get_or_create_buffer(self, container_name: str) -> TimeWindowBuffer:
        """Get existing buffer or create new one for container."""
        with self.lock:
            if container_name not in self.buffers:
                self.buffers[container_name] = TimeWindowBuffer(container_name)
                logger.info(f"Created buffer for container: {container_name}")
            return self.buffers[container_name]
    
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
        with self.lock:
            for container_name, buffer in self.buffers.items():
                logs = buffer.get_window(start_time, end_time)
                if logs:  # Only include containers with logs in window
                    results[container_name] = logs
        
        return results
    
    def cleanup_all(self, retention_seconds: int = 180):
        """Cleanup old logs from all buffers."""
        with self.lock:
            for buffer in self.buffers.values():
                buffer.cleanup_old_logs(retention_seconds)
    
    def get_stats(self) -> Dict[str, Dict]:
        """Get buffer statistics for monitoring."""
        stats = {}
        with self.lock:
            for container_name, buffer in self.buffers.items():
                time_range = buffer.get_time_range()
                stats[container_name] = {
                    'size': buffer.size(),
                    'oldest': time_range[0] if time_range else None,
                    'newest': time_range[1] if time_range else None,
                }
        return stats
    
    def remove_container(self, container_name: str):
        """Remove buffer for a stopped container."""
        with self.lock:
            if container_name in self.buffers:
                del self.buffers[container_name]
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
