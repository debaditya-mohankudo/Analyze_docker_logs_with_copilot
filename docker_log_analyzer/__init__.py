"""Docker Log Analyzer - Non-LLM Docker log pattern analysis via VSCode Copilot Agent Mode."""

__version__ = "0.3.0"

from docker_log_analyzer.mcp_server import run as run_mcp_server

__all__ = ["run_mcp_server"]
