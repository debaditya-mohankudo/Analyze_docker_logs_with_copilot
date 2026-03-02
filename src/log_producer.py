"""
Log Producer - Streams Docker container logs to Kafka.
Monitors containers and publishes their logs in real-time.
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import List, Optional

import docker
from docker.errors import DockerException, NotFound, APIError
from kafka import KafkaProducer
from kafka.errors import KafkaError

import config

logger = logging.getLogger(__name__)


class DockerLogProducer:
    """Streams logs from Docker containers to Kafka."""
    
    def __init__(self):
        self.docker_client = None
        self.kafka_producer = None
        self.streaming_threads = []
        self.stop_event = threading.Event()
        self.container_name = None  # Will store analyzer's own container name
        
        self._init_docker_client()
        self._init_kafka_producer()
    
    def _init_docker_client(self):
        """Initialize Docker client."""
        try:
            self.docker_client = docker.DockerClient(base_url=config.DOCKER_HOST)
            # Test connection
            self.docker_client.ping()
            logger.info(f"Connected to Docker at {config.DOCKER_HOST}")
            
            # Get own container name to exclude from monitoring
            try:
                import socket
                hostname = socket.gethostname()
                self.container_name = hostname
                logger.info(f"Running in container: {self.container_name}")
            except Exception as e:
                logger.warning(f"Could not determine own container name: {e}")
                
        except DockerException as e:
            logger.error(f"Failed to connect to Docker: {e}")
            raise
    
    def _init_kafka_producer(self):
        """Initialize Kafka producer with retry logic."""
        max_retries = 10
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                self.kafka_producer = KafkaProducer(
                    bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    acks=1,  # Wait for leader acknowledgment
                    retries=3,
                    max_in_flight_requests_per_connection=5
                )
                logger.info(f"Connected to Kafka at {config.KAFKA_BOOTSTRAP_SERVERS}")
                return
            except KafkaError as e:
                logger.warning(f"Kafka connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.error("Failed to connect to Kafka after all retries")
                    raise
    
    def discover_containers(self) -> List[docker.models.containers.Container]:
        """Discover containers to monitor based on filter."""
        try:
            filters = {}
            
            # Apply label filter if configured
            if config.CONTAINER_LABEL_FILTER:
                # Parse label filter: "key=value" or just "key"
                if '=' in config.CONTAINER_LABEL_FILTER:
                    key, value = config.CONTAINER_LABEL_FILTER.split('=', 1)
                    filters['label'] = [f"{key}={value}"]
                else:
                    filters['label'] = [config.CONTAINER_LABEL_FILTER]
            
            containers = self.docker_client.containers.list(filters=filters)
            
            # Exclude self from monitoring
            containers = [c for c in containers if c.name != self.container_name and c.id[:12] != self.container_name]
            
            logger.info(f"Discovered {len(containers)} containers to monitor")
            for container in containers:
                logger.info(f"  - {container.name} ({container.id[:12]})")
            
            return containers
            
        except DockerException as e:
            logger.error(f"Error discovering containers: {e}")
            return []
    
    def parse_docker_timestamp(self, timestamp_str: str) -> Optional[float]:
        """Parse Docker timestamp to Unix epoch float."""
        try:
            # Docker timestamp format: 2024-03-02T10:15:30.123456789Z
            # Remove nanoseconds (keep microseconds)
            if '.' in timestamp_str:
                base, fraction = timestamp_str.rsplit('.', 1)
                # Keep only first 6 digits (microseconds)
                fraction = fraction[:6] + 'Z'
                timestamp_str = f"{base}.{fraction}"
            
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            return dt.timestamp()
        except Exception as e:
            logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
            return time.time()  # Fallback to current time
    
    def stream_container_logs(self, container: docker.models.containers.Container):
        """Stream logs from a single container to Kafka."""
        container_name = container.name
        container_id = container.id[:12]
        
        logger.info(f"Starting log stream for {container_name}")
        
        while not self.stop_event.is_set():
            try:
                # Stream logs with timestamps
                for log_bytes in container.logs(stream=True, follow=True, timestamps=True, tail=0):
                    if self.stop_event.is_set():
                        break
                    
                    try:
                        # Decode log line
                        log_line = log_bytes.decode('utf-8').strip()
                        
                        if not log_line:
                            continue
                        
                        # Parse timestamp and log content
                        # Format: "2024-03-02T10:15:30.123456Z log message here"
                        if ' ' in log_line:
                            timestamp_str, log_content = log_line.split(' ', 1)
                            timestamp = self.parse_docker_timestamp(timestamp_str)
                        else:
                            # No timestamp in log
                            timestamp = time.time()
                            log_content = log_line
                        
                        # Create message
                        message = {
                            'container_name': container_name,
                            'container_id': container_id,
                            'timestamp': timestamp,
                            'log_line': log_content,
                        }
                        
                        # Publish to Kafka (use container name as partition key for ordering)
                        self.kafka_producer.send(
                            config.LOGS_TOPIC_NAME,
                            value=message,
                            key=container_name.encode('utf-8')
                        )
                        
                    except UnicodeDecodeError:
                        logger.warning(f"Non-UTF8 log from {container_name}, skipping")
                        continue
                    except Exception as e:
                        logger.error(f"Error processing log from {container_name}: {e}")
                        continue
                
            except NotFound:
                logger.warning(f"Container {container_name} not found, stopping stream")
                break
            except APIError as e:
                logger.error(f"Docker API error for {container_name}: {e}")
                if not self.stop_event.is_set():
                    time.sleep(5)  # Wait before retry
            except Exception as e:
                logger.error(f"Unexpected error streaming {container_name}: {e}")
                if not self.stop_event.is_set():
                    time.sleep(5)
        
        logger.info(f"Stopped log stream for {container_name}")
    
    def start_streaming(self):
        """Start streaming logs from all discovered containers."""
        containers = self.discover_containers()
        
        if not containers:
            logger.warning("No containers to monitor")
            return
        
        # Start a thread for each container
        for container in containers:
            thread = threading.Thread(
                target=self.stream_container_logs,
                args=(container,),
                name=f"stream-{container.name}",
                daemon=True
            )
            thread.start()
            self.streaming_threads.append(thread)
        
        logger.info(f"Started {len(self.streaming_threads)} streaming threads")
    
    def stop(self):
        """Stop all streaming threads."""
        logger.info("Stopping log producer...")
        self.stop_event.set()
        
        # Wait for threads to finish
        for thread in self.streaming_threads:
            thread.join(timeout=5)
        
        # Flush and close Kafka producer
        if self.kafka_producer:
            self.kafka_producer.flush()
            self.kafka_producer.close()
        
        logger.info("Log producer stopped")


if __name__ == '__main__':
    # Test the producer
    producer = DockerLogProducer()
    producer.start_streaming()
    
    try:
        # Keep running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        producer.stop()
