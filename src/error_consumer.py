"""
Error Consumer - Consumes logs from Kafka and detects errors.
Triggers analysis when errors are detected.
"""

import json
import re
import threading
import time
from typing import Dict, List, Callable, Optional

from kafka import KafkaConsumer
from kafka.errors import KafkaError

import config
from buffer_manager import BufferManager
from logger import logger


class ErrorDetector:
    """Detects errors in log lines using regex patterns."""
    
    def __init__(self, patterns: List[str] = None):
        self.patterns = patterns or config.ERROR_PATTERNS
        # Compile regex patterns (case-insensitive)
        self.compiled_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.patterns
        ]
        logger.info(f"Initialized error detector with {len(self.patterns)} patterns")
    
    def is_error(self, log_line: str) -> bool:
        """Check if log line matches any error pattern."""
        for pattern in self.compiled_patterns:
            if pattern.search(log_line):
                return True
        return False
    
    def get_matched_pattern(self, log_line: str) -> Optional[str]:
        """Return the first pattern that matches, or None."""
        for i, pattern in enumerate(self.compiled_patterns):
            if pattern.search(log_line):
                return self.patterns[i]
        return None


class ErrorEvent:
    """Represents an error event detected in logs."""
    
    def __init__(self, container_name: str, container_id: str, 
                 timestamp: float, log_line: str, pattern: str):
        self.container_name = container_name
        self.container_id = container_id
        self.timestamp = timestamp
        self.log_line = log_line
        self.pattern = pattern
        self.detected_at = time.time()
    
    def __repr__(self):
        return (f"ErrorEvent(container={self.container_name}, "
                f"timestamp={self.timestamp}, pattern={self.pattern})")


class ErrorDetectorConsumer:
    """Consumes logs from Kafka, detects errors, and manages buffers."""
    
    def __init__(self, buffer_manager: BufferManager, 
                 error_callback: Callable[[ErrorEvent], None]):
        self.buffer_manager = buffer_manager
        self.error_callback = error_callback
        self.error_detector = ErrorDetector()
        self.kafka_consumer = None
        self.stop_event = threading.Event()
        self.debounce_timer = None
        self.pending_errors: List[ErrorEvent] = []
        self.pending_errors_lock = threading.Lock()
        
        self._init_kafka_consumer()
    
    def _init_kafka_consumer(self):
        """Initialize Kafka consumer with retry logic."""
        max_retries = 10
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                self.kafka_consumer = KafkaConsumer(
                    config.LOGS_TOPIC_NAME,
                    bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS,
                    group_id=config.CONSUMER_GROUP_ID,
                    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                    auto_offset_reset='latest',  # Start from latest (not historical)
                    enable_auto_commit=True,
                    auto_commit_interval_ms=5000
                )
                logger.info(f"Connected to Kafka topic '{config.LOGS_TOPIC_NAME}'")
                return
            except KafkaError as e:
                logger.warning(f"Kafka consumer connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.error("Failed to connect to Kafka after all retries")
                    raise
    
    def _trigger_analysis(self):
        """Trigger analysis after debounce period (with smart filtering)."""
        with self.pending_errors_lock:
            if not self.pending_errors:
                return
            
            # Get the first error (earliest) as the primary trigger
            primary_error = self.pending_errors[0]
            error_count = len(self.pending_errors)
            
            logger.info(f"Debounce period ended. Evaluating {error_count} error(s)")
            
            # Smart LLM triggering based on analytics
            if self.buffer_manager.should_trigger_llm_analysis(
                error_threshold=config.LLM_ERROR_THRESHOLD,
                window_seconds=60,
                affected_containers_min=2
            ):
                logger.info(f"LLM analysis triggered for primary error: {primary_error}")
                self.error_callback(primary_error)
            else:
                logger.info(f"Skipping LLM analysis (below threshold). Errors: {error_count}")
            
            # Clear pending errors
            self.pending_errors.clear()
    
    def _handle_error_detected(self, error_event: ErrorEvent):
        """Handle a detected error with debouncing."""
        with self.pending_errors_lock:
            self.pending_errors.append(error_event)
            
            # Cancel existing timer if any
            if self.debounce_timer:
                self.debounce_timer.cancel()
            
            # Start new debounce timer
            if config.DEBOUNCE_SECONDS > 0:
                self.debounce_timer = threading.Timer(
                    config.DEBOUNCE_SECONDS,
                    self._trigger_analysis
                )
                self.debounce_timer.daemon = True
                self.debounce_timer.start()
                
                logger.debug(f"Error detected, debouncing for {config.DEBOUNCE_SECONDS}s")
            else:
                # No debouncing, trigger immediately
                self._trigger_analysis()
    
    def consume_and_detect(self):
        """Main consumption loop."""
        logger.info("Starting log consumption and error detection...")
        
        try:
            for message in self.kafka_consumer:
                if self.stop_event.is_set():
                    break
                
                try:
                    # Parse message
                    data = message.value
                    container_name = data['container_name']
                    container_id = data['container_id']
                    timestamp = data['timestamp']
                    log_line = data['log_line']
                    
                    # Store raw bytes for Polars analytics
                    raw_bytes = log_line.encode('utf-8', errors='replace')
                    
                    # Add to buffer (both string and bytes for dual use cases)
                    self.buffer_manager.add_log(container_name, timestamp, log_line, raw_bytes)
                    
                    # Check for errors
                    if self.error_detector.is_error(log_line):
                        pattern = self.error_detector.get_matched_pattern(log_line)
                        
                        error_event = ErrorEvent(
                            container_name=container_name,
                            container_id=container_id,
                            timestamp=timestamp,
                            log_line=log_line,
                            pattern=pattern
                        )
                        
                        logger.warning(f"Error detected in {container_name}: {log_line[:100]}")
                        self._handle_error_detected(error_event)
                    
                except KeyError as e:
                    logger.error(f"Invalid message format, missing key: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    continue
        
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        except Exception as e:
            logger.error(f"Error in consumption loop: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the consumer."""
        logger.info("Stopping error consumer...")
        self.stop_event.set()
        
        # Cancel debounce timer
        if self.debounce_timer:
            self.debounce_timer.cancel()
        
        # Close Kafka consumer
        if self.kafka_consumer:
            self.kafka_consumer.close()
        
        logger.info("Error consumer stopped")


if __name__ == '__main__':
    # Test the error detector
    detector = ErrorDetector()
    
    test_logs = [
        "INFO: Application started successfully",
        "ERROR: Database connection failed",
        "WARN: High memory usage detected",
        "CRITICAL: System failure imminent",
        "HTTP 500 Internal Server Error",
        "Exception in thread 'main' java.lang.NullPointerException",
    ]
    
    for log in test_logs:
        is_error = detector.is_error(log)
        pattern = detector.get_matched_pattern(log) if is_error else None
        print(f"[{'ERROR' if is_error else 'OK'}] {log[:50]} (matched: {pattern})")
