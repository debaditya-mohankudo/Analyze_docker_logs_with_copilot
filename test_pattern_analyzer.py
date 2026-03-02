#!/usr/bin/env python3
"""Test the log pattern analyzer with realistic container logs."""

import sys
sys.path.insert(0, 'src')

from log_pattern_analyzer import LogPatternAnalyzer, PatternDetector


def test_pattern_detector():
    """Test the pattern detector with various log formats."""
    print("=" * 70)
    print("TESTING PATTERN DETECTOR")
    print("=" * 70)
    
    detector = PatternDetector()
    
    # Test cases with different container types
    test_cases = {
        "Python Flask": [
            "2024-03-02T21:19:41.123Z [INFO] Flask app starting on 0.0.0.0:5000",
            "2024-03-02T21:19:42.456Z [DEBUG] Database connection: psycopg2.connect(...)",
            "2024-03-02T21:19:43.789Z [ERROR] ConnectionRefusedError: Failed to connect to Redis",
            "2024-03-02T21:19:44.101Z [INFO] Health check passed",
            "2024-03-02T21:19:45.202Z [WARNING] High memory usage: 85%",
            "2024-03-02T21:19:46.303Z [INFO] Health check passed",
            "2024-03-02T21:19:47.404Z [DEBUG] Traceback (most recent call last):",
            "2024-03-02T21:19:48.505Z [DEBUG] File \"/app/main.py\", line 42",
        ],
        "Java Spring Boot": [
            "2024-03-02 21:19:41.123 [INFO ] [main] Application started",
            "2024-03-02 21:19:42.456 [DEBUG] [main] Connection pool initialized",
            "2024-03-02 21:19:43.789 [ERROR] [main] Exception in thread",
            "2024-03-02 21:19:44.101 [ERROR] [main] at java.lang.NullPointerException",
            "2024-03-02 21:19:45.202 [INFO ] [scheduler] Health check: OK",
            "2024-03-02 21:19:46.303 [INFO ] [scheduler] Health check: OK",
            "2024-03-02 21:19:47.404 [WARN ] [main] Caused by: java.sql.SQLException",
        ],
        "Node.js Express": [
            "2024-03-02T21:19:41Z [info] Server listening on port 3000",
            "2024-03-02T21:19:42Z [debug] MongoDB connected to mongodb://db:27017",
            "2024-03-02T21:19:43Z [error] Error: ENOENT: no such file or directory",
            "2024-03-02T21:19:44Z [error] at process.js:123:4",
            "2024-03-02T21:19:45Z [info] GET /health 200 OK",
            "2024-03-02T21:19:46Z [info] GET /health 200 OK",
            "2024-03-02T21:19:47Z [error] npm ERR! code ELIFECYCLE",
        ],
        "PHP Laravel": [
            "[2024-03-02 21:19:41] production.ERROR: Database connection failed",
            "[2024-03-02 21:19:42] production.DEBUG: Laravel started",
            "[2024-03-02 21:19:43] production.ERROR: Exception: /app/Http/Controller.php:42",
            "[2024-03-02 21:19:44] production.ERROR: On line 42 in file /app/Http/Controller.php",
            "[2024-03-02 21:19:45] production.INFO: Health check passed",
            "[2024-03-02 21:19:46] production.INFO: Health check passed",
            "[2024-03-02 21:19:47] production.WARNING: Deprecated function called",
        ],
    }
    
    # Analyze each container type
    for container_type, logs in test_cases.items():
        print(f"\n📦 {container_type}")
        print("-" * 70)
        
        # Detect language
        language, confidence = detector.detect_language(logs)
        print(f"  Language: {language:12} ({confidence:.1%} confidence)")
        
        # Detect timestamp format
        ts_pattern = None
        for log in logs:
            ts_pattern = detector.detect_timestamp_format(log)
            if ts_pattern:
                break
        
        if ts_pattern:
            print(f"  Timestamp: {ts_pattern[0]:15} (sample: {ts_pattern[1][:35]}...)")
        else:
            print(f"  Timestamp: unknown")
        
        # Detect health checks
        health_check = detector.detect_health_checks(logs)
        if health_check:
            print(f"  Health Check: YES - pattern='{health_check.pattern}'")
            print(f"               frequency={health_check.frequency_per_minute:.1f}/min")
        else:
            print(f"  Health Check: NO")
        
        # Extract log levels
        levels = detector.extract_log_levels(logs)
        print(f"  Log Levels: {', '.join(sorted(levels.keys()))}")
        
        # Extract error patterns
        errors = detector.extract_error_patterns(logs)
        if errors:
            print(f"  Error Patterns: {len(errors)} detected")
            for pattern, count in errors[:2]:
                print(f"    - {pattern[:50]}: {count}x")


class MockBufferManager:
    """Mock buffer manager for testing."""
    
    def __init__(self):
        self.test_cases = {
            "Flask-API": [
                "2024-03-02T21:19:41.123Z [INFO] Flask app starting on 0.0.0.0:5000",
                "2024-03-02T21:19:42.456Z [DEBUG] Database connection: psycopg2.connect(...)",
                "2024-03-02T21:19:43.789Z [ERROR] ConnectionRefusedError: Failed to connect to Redis",
                "2024-03-02T21:19:44.101Z [INFO] Health check passed",
            ],
            "Java-Service": [
                "2024-03-02 21:19:41.123 [INFO ] [main] Application started",
                "2024-03-02 21:19:42.456 [DEBUG] [main] Connection pool initialized",
                "2024-03-02 21:19:45.202 [INFO ] [scheduler] Health check: OK",
                "2024-03-02 21:19:47.404 [WARN ] [main] Caused by: java.sql.SQLException",
            ],
        }
    
    def get_stats(self):
        """Return mock stats."""
        return {
            "Flask-API": {"oldest": 100.0, "newest": 200.0, "size": 4},
            "Java-Service": {"oldest": 100.0, "newest": 200.0, "size": 4},
        }
    
    def get_all_windows(self, start_time, end_time):
        """Return mock logs."""
        result = {}
        for container_name, logs in self.test_cases.items():
            result[container_name] = [(i, log) for i, log in enumerate(logs)]
        return result


def test_pattern_analyzer():
    """Test the full pattern analyzer."""
    print("\n" + "=" * 70)
    print("TESTING PATTERN ANALYZER")
    print("=" * 70)
    
    buffer_manager = MockBufferManager()
    analyzer = LogPatternAnalyzer(buffer_manager)
    
    # Analyze all containers
    patterns = analyzer.analyze_all_containers()
    
    print(f"\n✅ Analyzed {len(patterns)} containers")
    
    # Export to JSON
    success = analyzer.export_to_json("container_patterns_test.json")
    
    if success:
        print("\n✅ Successfully exported patterns to container_patterns_test.json")
        
        # Show summary
        summary = analyzer.get_pattern_summary()
        print(f"\nSummary: {summary['total_containers']} containers analyzed")
        
        return True
    
    return False


if __name__ == '__main__':
    try:
        test_pattern_detector()
        success = test_pattern_analyzer()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
