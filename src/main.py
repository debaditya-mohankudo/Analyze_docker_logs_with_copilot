"""
Main entry point for Docker Log Analyzer.
Orchestrates log streaming, error detection, and LLM analysis.
"""

import argparse
import signal
import sys
import threading
import time

import config
from buffer_manager import BufferManager
from log_producer import DockerLogProducer
from error_consumer import ErrorDetectorConsumer, ErrorEvent
from llm_analyzer import LLMAnalyzer
from log_pattern_analyzer import LogPatternAnalyzer
from logger import logger


class DockerLogAnalyzer:
    """Main orchestrator for the log analysis system."""
    
    def __init__(self):
        self.buffer_manager = BufferManager(
            enable_analytics=config.ANALYTICS_ENABLED,
            analytics_interval=config.ANALYTICS_INTERVAL
        )
        self.llm_analyzer = LLMAnalyzer(self.buffer_manager)
        self.pattern_analyzer = LogPatternAnalyzer(self.buffer_manager)
        self.log_producer = None
        self.error_consumer = None
        self.cleanup_thread = None
        self.stop_event = threading.Event()
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.stop()
        sys.exit(0)
    
    def _error_callback(self, error_event: ErrorEvent):
        """Callback function when error is detected."""
        logger.info(f"Processing error from {error_event.container_name}")
        
        # Trigger LLM analysis
        try:
            self.llm_analyzer.analyze(error_event)
        except Exception as e:
            logger.error(f"Failed to analyze error: {e}", exc_info=True)
    
    def _periodic_cleanup(self):
        """Periodically clean up old logs from buffers."""
        logger.info("Starting periodic cleanup thread")
        
        while not self.stop_event.is_set():
            try:
                # Wait for 30 seconds or until stop event
                if self.stop_event.wait(timeout=30):
                    break
                
                # Cleanup logs older than 3 minutes (180 seconds)
                self.buffer_manager.cleanup_all(retention_seconds=180)
                
                # Log buffer statistics
                stats = self.buffer_manager.get_stats()
                if stats:
                    logger.debug("Buffer statistics:")
                    for container, stat in stats.items():
                        logger.debug(f"  {container}: {stat['size']} logs")
                
            except Exception as e:
                logger.error(f"Error in cleanup thread: {e}")
        
        logger.info("Cleanup thread stopped")
    
    def start(self):
        """Start the log analyzer system."""
        try:
            # Validate and log configuration
            config.validate_config()
            config.log_config()
            
            logger.info("Starting Docker Log Analyzer...")
            
            # Start analytics thread if enabled
            if config.ANALYTICS_ENABLED:
                self.buffer_manager.start_analytics()
                logger.info("Polars analytics enabled")
            
            # Start periodic cleanup thread
            self.cleanup_thread = threading.Thread(
                target=self._periodic_cleanup,
                name="cleanup",
                daemon=True
            )
            self.cleanup_thread.start()
            
            # Initialize and start log producer
            logger.info("Initializing log producer...")
            self.log_producer = DockerLogProducer()
            self.log_producer.start_streaming()
            
            # Give producer a moment to start streaming
            time.sleep(2)
            
            # Initialize and start error consumer
            logger.info("Initializing error consumer...")
            self.error_consumer = ErrorDetectorConsumer(
                buffer_manager=self.buffer_manager,
                error_callback=self._error_callback
            )
            
            logger.info("=" * 80)
            logger.info("Docker Log Analyzer is running")
            logger.info("Monitoring containers for errors...")
            logger.info("Press Ctrl+C to stop")
            logger.info("=" * 80)
            
            # Start consuming (this blocks)
            self.error_consumer.consume_and_detect()
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
            self.stop()
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            self.stop()
            sys.exit(1)
    
    def analyze_patterns(self):
        """Analyze and save container log patterns to JSON."""
        try:
            logger.info("=" * 80)
            logger.info("Analyzing container log patterns...")
            logger.info("=" * 80)
            
            # Analyze all containers
            patterns = self.pattern_analyzer.analyze_all_containers()
            
            if not patterns:
                logger.warning("No patterns found - may need more logs in buffer")
                return False
            
            # Export to JSON
            success = self.pattern_analyzer.export_to_json("container_patterns.json")
            
            if success:
                logger.info("✅ Pattern analysis complete! Results saved to container_patterns.json")
            
            return success
            
        except Exception as e:
            logger.error(f"Pattern analysis failed: {e}", exc_info=True)
            return False
    
    def stop(self):
        """Stop all components."""
        logger.info("Stopping Docker Log Analyzer...")
        
        self.stop_event.set()
        
        # Stop analytics thread
        if config.ANALYTICS_ENABLED:
            self.buffer_manager.stop_analytics()
        
        # Stop error consumer
        if self.error_consumer:
            self.error_consumer.stop()
        
        # Stop log producer
        if self.log_producer:
            self.log_producer.stop()
        
        # Wait for cleanup thread
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=5)
        
        logger.info("Docker Log Analyzer stopped")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Docker Log Analyzer with LLM-powered correlation analysis"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Analyze log patterns from buffer and save to container_patterns.json"
    )
    parser.add_argument(
        "--collection-time",
        type=int,
        default=30,
        help="Seconds to collect logs before analyzing patterns (default: 30)"
    )
    
    args = parser.parse_args()
    
    print(r"""
    ____             __              __                 ___                __                     
   / __ \____  _____/ /_____  ____  / /   ____  ____ _/ _ |  ____  ____ _/ /_  ______  ___  _____
  / / / / __ \/ ___/ //_/ _ \/ __ \/ /   / __ \/ __ `/ __ | / __ \/ __ `/ / / / /_  / / _ \/ ___/
 / /_/ / /_/ / /__/ ,< /  __/ /_/ / /   / /_/ / /_/ / /_/ |/ / / / /_/ / / /_/ / / /_/  __/ /    
/_____/\____/\___/_/|_|\___/\____/_/    \____/\__, /_/ |_/_/ /_/\__,_/_/\__, / /___/\___/_/     
                                              /____/                    /____/                    
    """)
    
    print("Docker Log Analyzer with LLM-powered correlation analysis")
    print(f"Run ID: {logger.get_run_id()}")
    print("https://github.com/yourusername/docker-log-analyzer")
    print()
    
    try:
        analyzer = DockerLogAnalyzer()
        
        if args.analyze:
            # Pattern analysis mode
            logger.info(f"Starting log collection for {args.collection_time} seconds...")
            
            # Initialize producers only
            analyzer.log_producer = DockerLogProducer()
            analyzer.log_producer.start_streaming()
            
            # Give producer time to start
            time.sleep(2)
            
            # Initialize consumer only
            analyzer.error_consumer = ErrorDetectorConsumer(
                buffer_manager=analyzer.buffer_manager,
                error_callback=lambda e: None  # No-op callback
            )
            
            # Start a consumer thread to collect logs
            consumer_thread = threading.Thread(
                target=analyzer.error_consumer.consume_and_detect,
                daemon=True
            )
            consumer_thread.start()
            
            # Collect logs for specified time
            logger.info(f"Collecting logs for {args.collection_time} seconds...")
            time.sleep(args.collection_time)
            
            # Stop collection
            analyzer.error_consumer.stop()
            analyzer.log_producer.stop()
            
            # Analyze patterns
            analyzer.analyze_patterns()
            
        else:
            # Normal monitoring mode
            analyzer.start()
            
    except Exception as e:
        logger.error(f"Failed to start analyzer: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
