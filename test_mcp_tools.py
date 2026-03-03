#!/usr/bin/env python3
"""Test analyze_logs tool with sample Docker logs."""

import json
import sys
from docker_log_analyzer.mcp_server import (
    analyze_logs_with_llm,
    discover_log_patterns,
    correlate_container_errors
)

# Sample multi-container Docker logs
SAMPLE_LOGS = """
2026-03-03 06:30:15.123 [app-server] INFO: Application started on port 8080
2026-03-03 06:30:16.456 [app-server] ERROR: Database connection failed (connection timeout after 30s)
2026-03-03 06:30:17.789 [database] INFO: Connection attempt from 192.168.1.100:45678
2026-03-03 06:30:17.890 [database] ERROR: Authentication failed for user 'appuser' - password mismatch
2026-03-03 06:30:18.234 [app-server] ERROR: Failed to fetch user data - database unreachable
2026-03-03 06:30:19.567 [cache] INFO: Redis cache initialized on 6379
2026-03-03 06:30:20.123 [app-server] WARNING: Retrying database connection (attempt 1/3)
2026-03-03 06:30:22.456 [app-server] ERROR: Timeout waiting for database - giving up
2026-03-03 06:30:23.789 [database] ERROR: Connection pool exhausted (max connections: 100)
2026-03-03 06:30:25.012 [database] CRITICAL: Database replica sync failed
2026-03-03 06:30:26.345 [app-server] ERROR: Service degraded - database unavailable
2026-03-03 06:30:27.678 [api-gateway] WARNING: 5 requests failed with status 503
2026-03-03 06:30:28.901 [api-gateway] ERROR: Circuit breaker opened for database service
"""

def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def main():
    print_section("DOCKER LOG ANALYSIS TEST")
    
    # Show sample logs
    print("📋 Sample Docker Logs:")
    print("-" * 70)
    for line in SAMPLE_LOGS.strip().split('\n'):
        print(f"  {line}")
    print()
    
    # Test 1: Analyze logs with LLM
    print_section("TEST 1: Analyze Logs (LLM Analysis)")
    print("🤖 Calling analyze_logs_with_llm()...")
    print("-" * 70)
    
    analysis_result = analyze_logs_with_llm(SAMPLE_LOGS)
    
    if analysis_result["status"] == "success":
        print("✅ Analysis successful!\n")
        analysis = analysis_result.get("analysis", {})
        
        if "errors_detected" in analysis:
            print("🔴 Errors Detected:")
            for error in analysis.get("errors_detected", [])[:5]:
                print(f"  • [{error.get('severity', '?')}] {error.get('container', '?')}: {error.get('message', '?')}")
        
        if "root_cause" in analysis:
            print(f"\n🎯 Root Cause: {analysis['root_cause'].get('primary', 'Unknown')}")
            factors = analysis['root_cause'].get('contributing_factors', [])
            if factors:
                print("  Contributing factors:")
                for factor in factors[:3]:
                    print(f"    - {factor}")
        
        if "recommendations" in analysis:
            print(f"\n✅ Recommendations:")
            for rec in analysis.get("recommendations", [])[:5]:
                print(f"  • {rec}")
        
        print(f"\nFull analysis (JSON):")
        print(json.dumps(analysis, indent=2)[:500] + "...")
    else:
        print(f"❌ Analysis failed: {analysis_result.get('error', 'Unknown error')}")
    
    # Test 2: Discover patterns
    print_section("TEST 2: Discover Patterns")
    print("🔍 Calling discover_log_patterns()...")
    print("-" * 70)
    
    pattern_result = discover_log_patterns(SAMPLE_LOGS)
    
    if pattern_result["status"] == "success":
        print("✅ Pattern discovery successful!\n")
        patterns = pattern_result.get("patterns", {})
        
        print(f"📊 Statistics:")
        print(f"  • Total lines: {patterns.get('total_lines', 0)}")
        print(f"  • Error lines: {patterns.get('error_lines', 0)}")
        
        ts_format = patterns.get('timestamp_format', {})
        print(f"\n🕐 Timestamp Format: {ts_format.get('detected', 'unknown')}")
        
        lang = patterns.get('programming_language', {})
        print(f"🐍 Language: {lang.get('detected', 'unknown')}")
        
        print(f"\n📈 Log Levels:")
        for level, count in patterns.get('log_levels', {}).items():
            print(f"  • {level}: {count}")
        
        errors = patterns.get('error_sample', [])
        if errors:
            print(f"\n🔴 Error Sample (first 3):")
            for i, error in enumerate(errors[:3], 1):
                print(f"  {i}. {error[:80]}...")
    else:
        print(f"❌ Pattern discovery failed: {pattern_result.get('error', 'Unknown error')}")
    
    # Test 3: Correlate errors
    print_section("TEST 3: Correlate Errors")
    print("🔗 Calling correlate_container_errors()...")
    print("-" * 70)
    
    correlation_result = correlate_container_errors(SAMPLE_LOGS)
    
    if correlation_result["status"] == "success":
        print("✅ Correlation analysis successful!\n")
        correlation = correlation_result.get("correlation_analysis", {})
        
        print(f"📦 Container Analysis:")
        print(f"  • Total containers: {correlation.get('total_containers', 0)}")
        print(f"  • Containers with errors: {correlation.get('containers_with_errors', 0)}")
        print(f"  • Total errors: {correlation.get('total_errors', 0)}")
        
        correlations = correlation.get('correlations', [])
        if correlations:
            print(f"\n🔗 Error Correlations:")
            for corr in correlations[:5]:
                print(f"  • {corr['container']}: {corr['error_count']} errors")
        
        timeline = correlation.get('error_timeline', [])
        if timeline:
            print(f"\n⏱️ Error Timeline (first 3):")
            for i, event in enumerate(timeline[:3], 1):
                print(f"  {i}. [{event['container']}] {event['log'][:70]}...")
    else:
        print(f"❌ Correlation analysis failed: {correlation_result.get('error', 'Unknown error')}")
    
    print_section("TEST COMPLETE")
    print("✨ All tools executed successfully!")
    print("\nNext steps:")
    print("  1. Use these results as reference for Claude integration")
    print("  2. Configure Claude Desktop with MCP server")
    print("  3. Test with actual Docker container logs")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
