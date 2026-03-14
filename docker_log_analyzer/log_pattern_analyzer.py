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
import os
import re
import time
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .logger import logger
from .config import settings


# Error-pattern extraction uses three layers:
# 1. settings.error_patterns provides the configurable baseline catalog.
# 2. _EXTRACT_ERROR_PATTERNS appends broader summary-oriented shapes so
#    extract_error_patterns can still surface readable grouped snippets.
# 3. _ERROR_LINE_RE is a cheap prefilter so we only run the full pattern set
#    on lines that already look like errors.
_EXTRACT_ERROR_PATTERNS = [
    *settings.error_patterns,
    r"(Connection|Timeout|Failed|Error|Exception): [^:]*",
    r"(Database|API|Network|Service) error",
    r"Status code: \d{3}",
    r"(connect\(\) failed|upstream timed out|no live upstreams|SSL_do_handshake\(\) failed)[^,\n]*",
]
_COMPILED_EXTRACT_ERROR_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in _EXTRACT_ERROR_PATTERNS
]
_ERROR_LINE_RE = re.compile(
    "|".join(f"(?:{pattern})" for pattern in [*settings.error_patterns, r"\[(error|crit|alert|emerg)\]"]),
    re.IGNORECASE,
)


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
    language: str  # "python", "java", "php", "go", "nodejs", "nginx", etc.
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
        "nginx": (
            r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}",
            "Nginx format (2024/03/02 21:19:41)"
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
            # Spring Framework
            r"org\.springframework\.",
            r"BeanCreationException|NoSuchBeanDefinitionException|UnsatisfiedDependencyException",
            r"APPLICATION FAILED TO START",
            r"Error starting ApplicationContext",
            r"DispatcherServlet",
            r"HibernateException|org\.hibernate\.",
            r"DataAccessException|TransactionException",
            r"HttpMessageNotReadableException|MethodArgumentNotValidException",
            r"org\.springframework\.security\.(Authentication|Access)Exception",
            # Cassandra / DataStax driver
            r"com\.datastax\.(driver|oss\.driver)\.",
            r"NoHostAvailableException|AllNodesFailedException",
            r"ReadTimeoutException|WriteTimeoutException|UnavailableException",
            r"QueryExecutionException|InvalidQueryException|DriverException",
            # Kafka (Apache Kafka Java client)
            r"org\.apache\.kafka\.",
            r"KafkaException|ProducerFencedException|CommitFailedException",
            r"RecordTooLargeException|SerializationException|WakeupException",
            r"RebalanceInProgressException|OutOfOrderSequenceException",
            r"Broker: (Leader not available|Unknown topic or partition|Request timed out)",
        ],
        "php": [
            r"PHP\s*(Fatal|Warning|Notice)",
            r"On line \d+ in file",
            r"\.php:",
            r"WordPress|Laravel|Symfony|Slim\\",
            # Slim Framework
            r"Slim\\Exception\\Http(NotFound|MethodNotAllowed|Unauthorized|Forbidden|BadRequest|InternalServerError)Exception",
            r"Slim\\Routing\\RouteCollector|Slim\\Factory\\AppFactory",
            r"Slim\\Middleware\\",
            r"FastRoute\\",
            r"Psr\\Container\\ContainerExceptionInterface",
            # Kafka (php-rdkafka extension)
            r"RdKafka\\(Exception|KafkaErrorException)",
            r"rdkafka",
            r"Local: (Queue full|Message timed out|Broker transport failure)",
            r"Broker: (Leader not available|Unknown topic or partition)",
            r"UNKNOWN_TOPIC_OR_PART|OFFSET_OUT_OF_RANGE|GROUP_COORDINATOR_NOT_AVAILABLE",
            # MySQL errors
            r"PDOException|mysqli?_connect_error|mysqli?_error",
            r"SQLSTATE\[",
            r"Can't connect to MySQL server",
            r"MySQL server has gone away",
            r"Lost connection to MySQL server",
            r"Access denied for user.*MySQL",
            r"Duplicate entry.*for key",
            r"Table '.*' doesn't exist",
            r"Deadlock found when trying to get lock",
            r"Too many connections",
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
        "nginx": [
            r"nginx/\d+\.\d+",
            r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} \[(error|warn|crit|alert|emerg)\]",
            r"connect\(\) failed.*upstream",
            r"upstream timed out",
            r"no live upstreams while connecting to upstream",
            r"upstream prematurely closed connection",
            r"upstream sent invalid header",
            r"SSL_do_handshake\(\) failed",
            r"client intended to send too large body",
            r"open\(\) .* failed \(\d+: No such file or directory\)",
            r"FastCGI sent in stderr",
        ],
    }
    
    # Web framework detection (applied after language is confirmed)
    FRAMEWORK_PATTERNS: Dict[str, Dict[str, List[str]]] = {
        "java": {
            "spring": [
                r"org\.springframework\.",
                r"SpringApplication",
                r"DispatcherServlet",
                r"BeanCreationException|UnsatisfiedDependencyException",
                r"APPLICATION FAILED TO START",
            ],
            "quarkus": [
                r"io\.quarkus\.",
                r"Quarkus",
                r"quarkus-",
                r"io\.quarkus\.runtime",
            ],
            "micronaut": [
                r"io\.micronaut\.",
                r"Micronaut",
                r"io\.micronaut\.context",
            ],
            "vertx": [
                r"io\.vertx\.",
                r"Vert\.x",
                r"io\.vertx\.core",
            ],
            "helidon": [
                r"io\.helidon\.",
                r"Helidon",
            ],
            "wildfly": [
                r"org\.jboss\.",
                r"WildFly",
                r"javax\.ejb\.",
                r"org\.wildfly\.",
            ],
            "dropwizard": [
                r"io\.dropwizard\.",
                r"Dropwizard",
            ],
        },
    }

    # Compiled alternation regexes — built once from LANGUAGE_PATTERNS / FRAMEWORK_PATTERNS
    # at class-definition time so detect_language / detect_framework make one .search()
    # call per language per line instead of one call per pattern per line.
    _LANGUAGE_RE: Dict[str, re.Pattern] = {
        lang: re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
        for lang, pats in LANGUAGE_PATTERNS.items()
    }
    _FRAMEWORK_RE: Dict[str, Dict[str, re.Pattern]] = {
        lang: {
            fw: re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
            for fw, pats in frameworks.items()
        }
        for lang, frameworks in FRAMEWORK_PATTERNS.items()
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
            for language, combined_re in PatternDetector._LANGUAGE_RE.items():
                if combined_re.search(log_line):
                    scores[language] += 1

        if not scores:
            return ("unknown", 0.0)

        best_lang = max(scores, key=scores.get)
        best_score = scores[best_lang]

        # Normalize confidence (0-1)
        confidence = min(best_score / max(len(log_lines), 1), 1.0)

        return (best_lang, confidence)

    @staticmethod
    def detect_framework(language: str, log_lines: List[str]) -> Optional[str]:
        """Detect web framework for a given language.

        Currently supports Java frameworks: Spring, Quarkus, Micronaut,
        Vert.x, Helidon, WildFly, Dropwizard.
        Returns the framework name or None if undetected.
        """
        lang_frameworks = PatternDetector._FRAMEWORK_RE.get(language)
        if not lang_frameworks:
            return None

        scores: Dict[str, int] = defaultdict(int)
        for log_line in log_lines:
            for framework, combined_re in lang_frameworks.items():
                if combined_re.search(log_line):
                    scores[framework] += 1

        if not scores:
            return None
        return max(scores, key=lambda f: scores[f])

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
        level_pattern = r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL|TRACE|SEVERE|CRIT|ALERT|EMERG)\b|\[(error|warn|crit|alert|emerg|notice|info|debug)\]"
        
        for log_line in log_lines:
            match = re.search(level_pattern, log_line, re.IGNORECASE)
            if match:
                levels[(match.group(1) or match.group(2)).upper()] += 1
        
        return dict(levels)
    
    @staticmethod
    def extract_error_patterns(log_lines: List[str]) -> List[Tuple[str, int]]:
        """Extract common error patterns."""
        errors = Counter()

        for log_line in log_lines:
            if _ERROR_LINE_RE.search(log_line):
                for pattern_re in _COMPILED_EXTRACT_ERROR_PATTERNS:
                    match = pattern_re.search(log_line)
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
    
    def export_to_json(self, filepath: str = ".cache/patterns/container_patterns.json") -> bool:
        """Export detected patterns to JSON file."""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
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
            
            # Write to .cache/patterns/
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
