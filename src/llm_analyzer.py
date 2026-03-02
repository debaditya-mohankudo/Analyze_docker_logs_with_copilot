"""
LLM Analyzer - Aggregates log context and performs AI analysis.
Uses OpenAI to identify correlations and root causes.
"""

import logging
from datetime import datetime
from typing import Dict, List, Tuple

from openai import OpenAI

import config
from buffer_manager import BufferManager
from error_consumer import ErrorEvent

logger = logging.getLogger(__name__)


class ContextAggregator:
    """Aggregates log context from multiple containers."""
    
    def __init__(self, buffer_manager: BufferManager):
        self.buffer_manager = buffer_manager
    
    def get_context_window(self, error_event: ErrorEvent) -> Dict[str, List[Tuple[float, str]]]:
        """
        Retrieve ±CONTEXT_WINDOW_SECONDS of logs from all containers.
        Returns dict: {container_name: [(timestamp, log_line), ...]}
        """
        error_timestamp = error_event.timestamp
        start_time = error_timestamp - config.CONTEXT_WINDOW_SECONDS
        end_time = error_timestamp + config.CONTEXT_WINDOW_SECONDS
        
        logger.info(f"Aggregating context window: {start_time:.2f} to {end_time:.2f}")
        
        context = self.buffer_manager.get_all_windows(start_time, end_time)
        
        # Log statistics
        total_logs = sum(len(logs) for logs in context.values())
        logger.info(f"Aggregated {total_logs} logs from {len(context)} containers")
        
        return context
    
    def format_context_for_llm(self, error_event: ErrorEvent, 
                               context: Dict[str, List[Tuple[float, str]]]) -> str:
        """Format context logs into a readable string for LLM."""
        lines = []
        
        # Add header
        lines.append("=" * 80)
        lines.append("DOCKER CONTAINER LOG ANALYSIS")
        lines.append("=" * 80)
        lines.append("")
        
        # Add error details
        lines.append(f"ERROR DETECTED:")
        lines.append(f"  Container: {error_event.container_name}")
        lines.append(f"  Timestamp: {datetime.fromtimestamp(error_event.timestamp).isoformat()}")
        lines.append(f"  Pattern: {error_event.pattern}")
        lines.append(f"  Log: {error_event.log_line}")
        lines.append("")
        
        # Add context window info
        start_time = error_event.timestamp - config.CONTEXT_WINDOW_SECONDS
        end_time = error_event.timestamp + config.CONTEXT_WINDOW_SECONDS
        lines.append(f"CONTEXT WINDOW: ±{config.CONTEXT_WINDOW_SECONDS} seconds")
        lines.append(f"  Start: {datetime.fromtimestamp(start_time).isoformat()}")
        lines.append(f"  End: {datetime.fromtimestamp(end_time).isoformat()}")
        lines.append("")
        
        # Add logs from each container
        lines.append("LOGS BY CONTAINER:")
        lines.append("-" * 80)
        
        # Sort containers: error source first, then alphabetically
        sorted_containers = sorted(context.keys())
        if error_event.container_name in sorted_containers:
            sorted_containers.remove(error_event.container_name)
            sorted_containers.insert(0, error_event.container_name)
        
        for container_name in sorted_containers:
            logs = context[container_name]
            
            # Mark if this is the error source
            marker = " [ERROR SOURCE]" if container_name == error_event.container_name else ""
            lines.append(f"\n### {container_name}{marker} ({len(logs)} logs) ###")
            
            # Sort logs by timestamp
            sorted_logs = sorted(logs, key=lambda x: x[0])
            
            for timestamp, log_line in sorted_logs:
                time_str = datetime.fromtimestamp(timestamp).strftime('%H:%M:%S.%f')[:-3]
                lines.append(f"[{time_str}] {log_line}")
        
        lines.append("")
        lines.append("=" * 80)
        
        return "\n".join(lines)


class LLMAnalyzer:
    """Analyzes logs using OpenAI LLM."""
    
    def __init__(self, buffer_manager: BufferManager):
        self.buffer_manager = buffer_manager
        self.context_aggregator = ContextAggregator(buffer_manager)
        self.client = OpenAI(api_key=config.OPENAI_API_KEY)
        logger.info(f"Initialized LLM analyzer with model: {config.MODEL_NAME}")
    
    def analyze(self, error_event: ErrorEvent):
        """Analyze error with context from all containers."""
        try:
            # Aggregate context
            context = self.context_aggregator.get_context_window(error_event)
            
            if not context:
                logger.warning("No context logs available for analysis")
                return
            
            # Format context
            formatted_context = self.context_aggregator.format_context_for_llm(
                error_event, context
            )
            
            # Print formatted context to console
            print("\n" + formatted_context)
            
            # Build LLM prompt
            prompt = self._build_prompt(error_event, formatted_context)
            
            # Call OpenAI API
            logger.info("Calling LLM for analysis...")
            response = self.client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert DevOps engineer analyzing Docker container logs to identify root causes and correlations in distributed systems."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,  # Lower temperature for more focused analysis
                max_tokens=1500
            )
            
            analysis = response.choices[0].message.content
            
            # Print analysis
            self._print_analysis(analysis)
            
        except Exception as e:
            logger.error(f"Error during LLM analysis: {e}", exc_info=True)
    
    def _build_prompt(self, error_event: ErrorEvent, formatted_context: str) -> str:
        """Build the prompt for LLM analysis."""
        prompt = f"""Analyze the following Docker container logs from a distributed system.

{formatted_context}

ANALYSIS TASKS:
1. **Root Cause**: Identify the most likely root cause of the error
2. **Correlations**: Find any related errors or warnings in other containers
3. **Timeline**: Describe the sequence of events leading to the error
4. **Impact**: Assess which services/containers were affected
5. **Recommendations**: Suggest specific actions to fix or prevent this issue

Provide a clear, concise analysis focusing on actionable insights."""
        
        return prompt
    
    def _print_analysis(self, analysis: str):
        """Print formatted analysis to console."""
        print("\n" + "=" * 80)
        print("LLM ANALYSIS RESULTS")
        print("=" * 80)
        print(analysis)
        print("=" * 80 + "\n")


if __name__ == '__main__':
    # Test the formatter
    from buffer_manager import BufferManager
    import time
    
    buffer_manager = BufferManager()
    
    # Add test data
    current_time = time.time()
    buffer_manager.add_log("web-server", current_time - 30, "GET /api/users 200 OK")
    buffer_manager.add_log("web-server", current_time - 10, "ERROR: Database connection timeout")
    buffer_manager.add_log("database", current_time - 15, "WARN: Connection pool exhausted")
    buffer_manager.add_log("database", current_time - 5, "ERROR: Too many connections")
    
    # Create test error event
    error_event = ErrorEvent(
        container_name="web-server",
        container_id="abc123",
        timestamp=current_time - 10,
        log_line="ERROR: Database connection timeout",
        pattern="ERROR"
    )
    
    # Test context aggregation
    aggregator = ContextAggregator(buffer_manager)
    context = aggregator.get_context_window(error_event)
    formatted = aggregator.format_context_for_llm(error_event, context)
    
    print(formatted)
