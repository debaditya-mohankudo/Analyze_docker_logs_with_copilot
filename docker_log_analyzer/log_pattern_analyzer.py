"""
Log Pattern Analyzer - Discovers container log characteristics.
Analyzes logs from each container to identify:
1. Timestamp formats (ISO-8601, syslog, epoch, custom)
2. Programming language (Python, Java, Go, PHP, Node.js, etc.)
3. Repeating health check patterns
4. Log level distributions
5. Common error patterns per container

Saves results to container_patterns.json for buffer manager optimization.
"""

import json
import re
import time
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .logger import logger


@dataclass
class TimestampPattern:
    """Represents a discovered timestamp format."""
    format_type: str  # "iso8601", "syslog", "epoch", "custom"
    sample: str
    regex_pattern: str
    confidence: float  # 0-1


@dataclass
class HealthCheckPattern:
    """Represents a repeating health check log."""
    pattern: str
    frequency_per_minute: float
    example_logs: List[str]
    confidence: float


@dataclass
class ContainerPattern:
    """Represents discovered patterns for a single container."""
    container_name: str
    container_id: str
    language: str  # "python", "java", "php", "go", "nodejs", etc.
    language_confidence: float
    timestamp_format: TimestampPattern
    log_levels: Dict[str, int]  # distribution of log levels
    health_check: Optional[HealthCheckPattern]
    common_errors: List[Tuple[str, int]]  # (error_pattern, count)
    sample_logs: List[str]
    analysis_time: float


class PatternDetector:
    """Detects patterns in log lines."""
    
    # Timestamp patterns
    TIMESTAMP_PATTERNS = {
        "iso8601": (
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
            "ISO-8601 format (2024-03-02T21:19:41)"
        ),
        "syslog": (
            r"^[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}",
            "Syslog format (Mar 2 21:19:41)"
        ),
        "epoch": (
            r"^\d{10}(\.\d+)?(\s|\[|$)",
            "Unix epoch timestamp"
        ),
        "apache": (
            r"^\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}",
            "Apache format (02/Mar/2024:21:19:41)"
        ),
    }
    
    # Language detection patterns
    LANGUAGE_PATTERNS = {
        "python": [
            r"Traceback \(most recent call last\)",
            r"File \".*\", line \d+",
            r"python\d+\.\d+",
            r"site-packages",
            r"ImportError|ModuleNotFoundError|AttributeError",
        ],
        "java": [
            r"Exception in thread",
            r"at java\.",
            r"java\.lang\.",
            r"Caused by:",
            r"pom\.xml|gradle\.build",
        ],
        "php": [
            r"PHP\s*(Fatal|Warning|Notice)",
            r"On line \d+ in file",
            r"\.php:",
            r"WordPress|Laravel|Symfony",
        ],
        "go": [
            r"panic:",
            r"goroutine \d+ \[",
            r"go version",
            r"runtime error",
        ],
        "nodejs": [
            r"at .*\(.*\.js:\d+:\d+\)",
            r"Error: ",
            r"node_modules",
            r"npm ERR!",
        ],
    }
    
    # Health check patterns (repeating, low-noise logs)
    HEALTH_CHECK_PATTERNS = [
        r"health check (passed|ok|successful)",
        r"ping pong",
        r"heartbeat",
        r"liveness probe",
        r"readiness probe",
        r"status: healthy",
        r"keep-alive",
        r"/health|/status|/ping",
        r"uptime:|alive:|running:",
    ]
    
    @staticmethod
    def detect_timestamp_format(log_line: str) -> Optional[Tuple[str, str, float]]:
        """
        Detect timestamp format in log line.
        Returns: (format_type, sample, confidence)
        """
        for format_type, (pattern, description) in PatternDetector.TIMESTAMP_PATTERNS.items():
            if re.search(pattern, log_line):
                return (format_type, log_line[:50], 0.9)
        return None
    
    @staticmethod
    def detect_language(log_lines: List[str]) -> Tuple[str, float]:
        """
        Detect programming language from logs.
        Returns: (language, confidence)
        """
        scores = defaultdict(int)
        
        for log_line in log_lines:
            for language, patterns in PatternDetector.LANGUAGE_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, log_line, re.IGNORECASE):
                        scores[language] += 1
        
        if not scores:
            return ("unknown", 0.0)
        
        best_lang = max(scores, key=scores.get)
        best_score = scores[best_lang]
        
        # Normalize confidence (0-1)
        confidence = min(best_score / max(len(log_lines), 1), 1.0)
        
        return (best_lang, confidence)
    
    @staticmethod
    def detect_health_checks(log_lines: List[str]) -> Optional[HealthCheckPattern]:
        """
        Detect repeating health check logs.
        Returns: HealthCheckPattern if found, else None
        """
        health_checks = defaultdict(list)
        
        for log_line in log_lines:
            for pattern in PatternDetector.HEALTH_CHECK_PATTERNS:
                if re.search(pattern, log_line, re.IGNORECASE):
                    health_checks[pattern].append(log_line)
        
        if not health_checks:
            return None
        
        # Find most frequent health check
        best_pattern = max(health_checks, key=lambda p: len(health_checks[p]))
        examples = health_checks[best_pattern][:3]
        
        frequency = len(examples) / max(len(log_lines), 1) * 60  # per minute estimate
        
        return HealthCheckPattern(
            pattern=best_pattern,
            frequency_per_minute=frequency,
            example_logs=examples,
            confidence=min(len(examples) / max(len(log_lines), 1), 1.0)
        )
    
    @staticmethod
    def extract_log_levels(log_lines: List[str]) -> Dict[str, int]:
        """Count distribution of log levels."""
        levels = Counter()
        level_pattern = r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL|TRACE|SEVERE)\b"
        
        for log_line in log_lines:
            match = re.search(level_pattern, log_line, re.IGNORECASE)
            if match:
                levels[match.group(1).upper()] += 1
        
        return dict(levels)
    
    @staticmethod
    def extract_error_patterns(log_lines: List[str]) -> List[Tuple[str, int]]:
        """Extract common error patterns."""
        errors = Counter()
        error_patterns = [
            r"(Connection|Timeout|Failed|Error|Exception): [^:]*",
            r"(Database|API|Network|Service) error",
            r"Status code: \d{3}",
        ]
        
        for log_line in log_lines:
            if re.search(r"ERROR|CRITICAL|FATAL|Exception", log_line, re.IGNORECASE):
                for pattern in error_patterns:
                    match = re.search(pattern, log_line)
                    if match:
                        errors[match.group(0)] += 1
        
        return errors.most_common(10)


class LogPatternAnalyzer:
    """Analyzes container log patterns from buffer manager."""
    
    def __init__(self, buffer_manager):
        self.buffer_manager = buffer_manager
        self.detector = PatternDetector()
        self.container_patterns: Dict[str, ContainerPattern] = {}
        logger.info("LogPatternAnalyzer initialized")
    
    def analyze_container(self, container_name: str, container_id: str, 
                         log_lines: List[str]) -> ContainerPattern:
        """Analyze logs from a single container."""
        if not log_lines:
            logger.warning(f"No logs to analyze for {container_name}")
            return None
        
        start_time = time.time()
        logger.info(f"Analyzing {len(log_lines)} logs from {container_name}")
        
        # Detect timestamp format
        timestamp_format = None
        for log_line in log_lines[:100]:  # Sample first 100
            ts_pattern = self.detector.detect_timestamp_format(log_line)
            if ts_pattern:
                timestamp_format = TimestampPattern(
                    format_type=ts_pattern[0],
                    sample=ts_pattern[1],
                    regex_pattern="",
                    confidence=ts_pattern[2]
                )
                break
        
        if not timestamp_format:
            timestamp_format = TimestampPattern(
                format_type="unknown",
                sample="",
                regex_pattern="",
                confidence=0.0
            )
        
        # Detect programming language
        language, lang_confidence = self.detector.detect_language(log_lines)
        
        # Detect health checks
        health_check = self.detector.detect_health_checks(log_lines)
        
        # Extract log levels
        log_levels = self.detector.extract_log_levels(log_lines)
        
        # Extract error patterns
        error_patterns = self.detector.extract_error_patterns(log_lines)
        
        pattern = ContainerPattern(
            container_name=container_name,
            container_id=container_id,
            language=language,
            language_confidence=lang_confidence,
            timestamp_format=timestamp_format,
            log_levels=log_levels,
            health_check=health_check,
            common_errors=error_patterns,
            sample_logs=log_lines[:5],
            analysis_time=time.time() - start_time
        )
        
        self.container_patterns[container_name] = pattern
        logger.info(
            f"✓ Analyzed {container_name}: language={language} "
            f"({lang_confidence:.1%}), timestamp={timestamp_format.format_type}, "
            f"health_check={'Yes' if health_check else 'No'}"
        )
        
        return pattern
    
    def analyze_all_containers(self) -> Dict[str, ContainerPattern]:
        """Analyze logs from all containers in buffer manager."""
        self.container_patterns.clear()
        
        stats = self.buffer_manager.get_stats()
        logger.info(f"Analyzing patterns from {len(stats)} containers...")
        
        for container_name in stats.keys():
            # Get all logs in buffer for this container
            time_range = stats[container_name].get('oldest'), stats[container_name].get('newest')
            
            if time_range[0] and time_range[1]:
                logs = self.buffer_manager.get_all_windows(time_range[0], time_range[1])
                if container_name in logs:
                    log_lines = [log_tuple[1] for log_tuple in logs[container_name]]
                    container_id = container_name  # Use name as ID if not available
                    self.analyze_container(container_name, container_id, log_lines)
        
        return self.container_patterns
    
    def export_to_json(self, filepath: str = "container_patterns.json") -> bool:
        """Export detected patterns to JSON file."""
        try:
            patterns_dict = {}
            
            for container_name, pattern in self.container_patterns.items():
                patterns_dict[container_name] = {
                    "container_name": pattern.container_name,
                    "container_id": pattern.container_id,
                    "language": {
                        "name": pattern.language,
                        "confidence": round(pattern.language_confidence, 3)
                    },
                    "timestamp_format": {
                        "type": pattern.timestamp_format.format_type,
                        "sample": pattern.timestamp_format.sample,
                        "confidence": round(pattern.timestamp_format.confidence, 3)
                    },
                    "log_levels": pattern.log_levels,
                    "health_check": {
                        "detected": pattern.health_check is not None,
                        "pattern": pattern.health_check.pattern if pattern.health_check else None,
                        "frequency_per_minute": round(pattern.health_check.frequency_per_minute, 2) if pattern.health_check else None,
                        "example_logs": pattern.health_check.example_logs if pattern.health_check else [],
                        "confidence": round(pattern.health_check.confidence, 3) if pattern.health_check else 0.0
                    },
                    "common_errors": [
                        {"pattern": err[0], "count": err[1]}
                        for err in pattern.common_errors
                    ],
                    "analysis_time_seconds": round(pattern.analysis_time, 3)
                }
            
            # Write to project root
            with open(filepath, 'w') as f:
                json.dump(patterns_dict, f, indent=2)
            
            logger.info(f"✓ Exported container patterns to {filepath}")
            
            # Also log summary
            logger.info("=" * 70)
            logger.info("CONTAINER PATTERN SUMMARY")
            logger.info("=" * 70)
            for container_name, data in patterns_dict.items():
                logger.info(f"\n📦 {container_name}")
                logger.info(f"   Language: {data['language']['name']} ({data['language']['confidence']:.1%})")
                logger.info(f"   Timestamp: {data['timestamp_format']['type']}")
                logger.info(f"   Health Checks: {'Yes' if data['health_check']['detected'] else 'No'}")
                logger.info(f"   Log Levels: {', '.join(data['log_levels'].keys())}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to export patterns: {e}", exc_info=True)
            return False
    
    def get_pattern_summary(self) -> Dict:
        """Get summary of all analyzed patterns."""
        summary = {
            "total_containers": len(self.container_patterns),
            "analysis_timestamp": datetime.now().isoformat(),
            "containers": {}
        }
        
        for container_name, pattern in self.container_patterns.items():
            summary["containers"][container_name] = {
                "language": pattern.language,
                "language_confidence": round(pattern.language_confidence, 3),
                "timestamp_format": pattern.timestamp_format.format_type,
                "has_health_checks": pattern.health_check is not None,
                "log_level_count": len(pattern.log_levels),
                "error_patterns_detected": len(pattern.common_errors)
            }
        
        return summary


if __name__ == '__main__':
    # Test pattern detection
    test_logs = [
        "2024-03-02T21:19:41.123Z [INFO] Application started successfully",
        "2024-03-02T21:19:42.456Z [DEBUG] Database connection established",
        "2024-03-02T21:19:43.789Z [ERROR] Failed to connect to API: Connection timeout",
        "2024-03-02T21:19:44.101Z [INFO] Health check passed",
        "2024-03-02T21:19:45.202Z [WARNING] High memory usage detected",
        "2024-03-02T21:19:46.303Z [INFO] Health check passed",
        "Exception in thread: java.lang.NullPointerException at Main.java:42",
    ]
    
    detector = PatternDetector()
    
    print("Timestamp Detection:")
    for log in test_logs[:2]:
        ts = detector.detect_timestamp_format(log)
        if ts:
            print(f"  ✓ {ts[0]}: {ts[1]}")
    
    print("\nLanguage Detection:")
    lang, conf = detector.detect_language(test_logs)
    print(f"  ✓ {lang} ({conf:.1%})")
    
    print("\nHealth Check Detection:")
    hc = detector.detect_health_checks(test_logs)
    if hc:
        print(f"  ✓ Pattern: {hc.pattern}")
        print(f"    Frequency: {hc.frequency_per_minute:.1f}/min")
    
    print("\nLog Levels:")
    levels = detector.extract_log_levels(test_logs)
    print(f"  {levels}")
