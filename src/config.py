"""
Configuration module for Docker Log Analyzer.
Loads settings from environment variables with validation.
"""

import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Logging Configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# OpenAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
MODEL_NAME = os.getenv('MODEL_NAME', 'gpt-4o-mini')

# Kafka Configuration
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
LOGS_TOPIC_NAME = os.getenv('LOGS_TOPIC_NAME', 'docker-logs')
CONSUMER_GROUP_ID = os.getenv('CONSUMER_GROUP_ID', 'log-analyzer-group')

# Docker Configuration
DOCKER_HOST = os.getenv('DOCKER_HOST', 'unix:///var/run/docker.sock')
CONTAINER_LABEL_FILTER = os.getenv('CONTAINER_LABEL_FILTER', '')

# Analysis Configuration
CONTEXT_WINDOW_SECONDS = int(os.getenv('CONTEXT_WINDOW_SECONDS', '60'))
DEBOUNCE_SECONDS = int(os.getenv('DEBOUNCE_SECONDS', '10'))

# Buffer Configuration
BUFFER_SIZE_PER_CONTAINER = int(os.getenv('BUFFER_SIZE_PER_CONTAINER', '1000'))

# Error Detection Patterns
ERROR_PATTERNS = [
    r'ERROR',
    r'CRITICAL',
    r'FATAL',
    r'Exception',
    r'Traceback',
    r'5\d{2}',  # HTTP 5xx errors
    r'panic:',  # Go panics
    r'SEVERE',  # Java severe errors
]


def validate_config():
    """Validate critical configuration settings."""
    errors = []
    
    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is required")
    
    if CONTEXT_WINDOW_SECONDS <= 0:
        errors.append("CONTEXT_WINDOW_SECONDS must be positive")
    
    if DEBOUNCE_SECONDS < 0:
        errors.append("DEBOUNCE_SECONDS must be non-negative")
    
    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        raise ValueError(f"Invalid configuration: {', '.join(errors)}")
    
    logger.info("Configuration validated successfully")


def log_config():
    """Log current configuration (without sensitive data)."""
    logger.info("=== Configuration ===")
    logger.info(f"Model: {MODEL_NAME}")
    logger.info(f"Kafka: {KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Topic: {LOGS_TOPIC_NAME}")
    logger.info(f"Docker Host: {DOCKER_HOST}")
    logger.info(f"Context Window: {CONTEXT_WINDOW_SECONDS}s")
    logger.info(f"Debounce: {DEBOUNCE_SECONDS}s")
    logger.info(f"Container Filter: {CONTAINER_LABEL_FILTER or 'None (all containers)'}")
    logger.info("====================")


if __name__ == '__main__':
    # Test configuration
    validate_config()
    log_config()
