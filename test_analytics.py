#!/usr/bin/env python3
"""Test script for Polars analytics integration."""

import time
from docker_log_analyzer.buffer_manager import BufferManager

def test_analytics():
    """Test Polars analytics functionality."""
    print("Testing Polars Analytics Integration...")
    print("=" * 60)
    
    # Create buffer manager with analytics
    manager = BufferManager(enable_analytics=True, analytics_interval=1.0)
    print("✓ BufferManager created with analytics enabled")
    
    # Add test logs
    current_time = time.time()
    manager.add_log('app1', current_time - 10, 'INFO: Application started')
    manager.add_log('app1', current_time - 5, 'ERROR: Database connection failed')
    manager.add_log('app2', current_time - 3, 'ERROR: API timeout')
    manager.add_log('app2', current_time - 1, 'CRITICAL: System failure')
    print("✓ Added 4 test log entries (2 per container)")
    
    # Test analytics snapshot
    df = manager.get_analytics_snapshot()
    print(f"✓ DataFrame created: {df.height} rows, {df.width} columns")
    print(f"  Schema: {df.columns}")
    
    # Test error rate calculation
    error_rate = manager.get_error_rate(window_seconds=60)
    print(f"✓ Error rates calculated: {error_rate}")
    print(f"  app1: {error_rate.get('app1', 0)} errors")
    print(f"  app2: {error_rate.get('app2', 0)} errors")
    
    # Test smart LLM triggering - should trigger (3 errors >= threshold)
    should_trigger = manager.should_trigger_llm_analysis(
        error_threshold=3, 
        affected_containers_min=2
    )
    print(f"✓ LLM trigger (threshold=3, min_containers=2): {should_trigger}")
    assert should_trigger == True, "Should trigger with 3 errors across 2 containers"
    
    # Test smart LLM triggering - should STILL trigger (2 containers >= min_containers)
    # Note: Even though 3 < 10, we trigger because 2 containers have errors
    should_trigger_containers = manager.should_trigger_llm_analysis(
        error_threshold=10, 
        affected_containers_min=2
    )
    print(f"✓ LLM trigger (threshold=10, min_containers=2): {should_trigger_containers}")
    assert should_trigger_containers == True, "Should trigger with 2 affected containers (multi-container correlation)"
    
    # Test smart LLM triggering - should NOT trigger (high thresholds for both)
    should_not_trigger = manager.should_trigger_llm_analysis(
        error_threshold=10, 
        affected_containers_min=5
    )
    print(f"✓ LLM trigger (threshold=10, min_containers=5): {should_not_trigger}")
    assert should_not_trigger == False, "Should not trigger when both conditions fail"
    
    # Test background analytics (run once manually)
    manager.run_analytics()
    last_analytics = manager.get_last_analytics()
    print(f"✓ Background analytics executed")
    print(f"  Total logs in 3min window: {last_analytics.get('total_logs', 0)}")
    
    print("=" * 60)
    print("✅ All Polars analytics tests PASSED!")
    
    return True

if __name__ == '__main__':
    try:
        success = test_analytics()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
