"""
Secret & sensitive data detection in Docker container logs.

Detects patterns for:
  - API keys (AWS, GitHub, Google, Stripe, generic)
  - Auth tokens & credentials (Bearer, JWT, OAuth, Slack)
  - Database connection strings
  - Private keys (RSA, EC, OpenSSH, DSA)
  - Azure storage account keys
  - PII (emails, credit cards)
  - Base64-encoded secrets
  - Session cookies
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class SecretPattern:
    """Represents a detectable secret pattern."""
    
    pattern: str  # Regex pattern
    name: str  # Human-readable name
    severity: str  # "critical", "high", "medium", "low"
    description: str


@dataclass
class Finding:
    """Found secret in logs."""
    
    severity: str
    pattern_name: str
    line_number: int
    timestamp: Optional[str]  # Extracted from log line if available
    context_before: str
    context_after: str
    matched_text_redacted: str  # Never return full secret


class SecretDetector:
    """Detects sensitive data patterns in log lines."""
    
    def __init__(self):
        """Initialize with predefined secret patterns."""
        self.patterns = [
            # === CRITICAL SEVERITY ===
            # AWS Access Keys (AKIA followed by 16 alphanumeric characters = 20 total)
            SecretPattern(
                pattern=r"AKIA[0-9A-Z]{16}",
                name="AWS Access Key ID",
                severity="critical",
                description="AWS API key format AKIA* (20 chars total)",
            ),
            # AWS Secret Keys
            SecretPattern(
                pattern=r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{40}",
                name="AWS Secret Access Key",
                severity="critical",
                description="AWS secret key assignment",
            ),
            # Private Keys (RSA, EC, OpenSSH)
            SecretPattern(
                pattern=r"-----BEGIN\s+(RSA|EC|OPENSSH|PRIVATE|DSA)\s+PRIVATE\s+KEY",
                name="Private Key Header",
                severity="critical",
                description="Private key file content detected",
            ),
            # GitHub Tokens (40+ chars for PAT)
            SecretPattern(
                pattern=r"gh[pousr]{1,2}_[A-Za-z0-9_]{36,}",
                name="GitHub Token",
                severity="critical",
                description="GitHub personal/OAuth/app token",
            ),
            # Stripe Secret Keys (live)
            SecretPattern(
                pattern=r"sk_live_[0-9a-zA-Z]{24}",
                name="Stripe Secret Key",
                severity="critical",
                description="Stripe live secret API key",
            ),

            # === HIGH SEVERITY ===
            # Generic API Keys (common patterns) - case insensitive matching
            SecretPattern(
                pattern=r"(?:api[_-]?key|apikey|api_secret)\s*[:=]\s*[A-Za-z0-9\-_]{32,}",
                name="Generic API Key",
                severity="high",
                description="API key assignment pattern",
            ),
            # Bearer Tokens (with optional refresh tokens)
            SecretPattern(
                pattern=r"[Bb]earer\s+[A-Za-z0-9\-._~+/]+=*",
                name="Bearer Token",
                severity="high",
                description="OAuth bearer token",
            ),
            # Database Connection Strings (with protocol)
            SecretPattern(
                pattern=r"(?:postgres|mysql|mongodb|redis)://[^:/@]+:[^/@]+@",
                name="Database URL with Credentials",
                severity="high",
                description="Database connection string with username/password",
            ),
            # Slack/Discord Tokens
            SecretPattern(
                pattern=r"xox[baprs]{1}-[0-9]{12,13}-[A-Za-z0-9\-_]{32}",
                name="Slack Token",
                severity="high",
                description="Slack bot/app token",
            ),
            # JWT Tokens (3 base64 parts separated by dots)
            SecretPattern(
                pattern=r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                name="JWT Token",
                severity="high",
                description="JSON Web Token (JWT)",
            ),
            # Google API Keys
            SecretPattern(
                pattern=r"AIza[0-9A-Za-z\-_]{35}",
                name="Google API Key",
                severity="high",
                description="Google Cloud API key",
            ),
            # Stripe Publishable Keys (live)
            SecretPattern(
                pattern=r"pk_live_[0-9a-zA-Z]{24}",
                name="Stripe Publishable Key",
                severity="high",
                description="Stripe live publishable API key",
            ),
            # Azure Storage Account Keys
            SecretPattern(
                pattern=r"AccountKey=[A-Za-z0-9+/=]{40,}",
                name="Azure Storage Account Key",
                severity="high",
                description="Azure storage account key in connection string",
            ),
            # OAuth Client Secrets
            SecretPattern(
                pattern=r"client_secret\s*[:=]\s*[A-Za-z0-9\-_]{24,}",
                name="OAuth Client Secret",
                severity="high",
                description="OAuth client secret detected",
            ),

            # === MEDIUM SEVERITY ===
            # Password assignments (with quotes and minimum length)
            SecretPattern(
                pattern=r"(?:password|passwd|pwd)\s*[:=]\s*['\"]([^'\"]{8,})['\"]",
                name="Password Assignment",
                severity="medium",
                description="Password variable assignment with quotes",
            ),
            # Email addresses (PII)
            SecretPattern(
                pattern=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
                name="Email Address",
                severity="medium",
                description="Email address (PII)",
            ),
            # Credit card patterns (flexible spacing)
            SecretPattern(
                pattern=r"\b(?:\d{4}[\s\-]?){3}\d{4}\b",
                name="Credit Card Number",
                severity="medium",
                description="Credit/debit card number pattern",
            ),
            # Generic secret/token assignments
            SecretPattern(
                pattern=r"(?:secret|token|credential)\s*[:=]\s*['\"]([^'\"]{16,})['\"]",
                name="Secret Assignment",
                severity="medium",
                description="Generic secret/token variable",
            ),
            # Base64-encoded secrets (key/token/secret assignments without quotes)
            SecretPattern(
                pattern=r"(?:secret|token|key)\s*[:=]\s*[A-Za-z0-9+/]{40,}={0,2}",
                name="Base64 Encoded Secret",
                severity="medium",
                description="Possible base64-encoded secret value",
            ),
            # Session cookies
            SecretPattern(
                pattern=r"sessionid=[A-Za-z0-9%\-._~+/]+=*",
                name="Session Cookie",
                severity="medium",
                description="Session token exposed in logs",
            ),
        ]
        
        # Pre-compile all patterns for performance
        self._compiled = {p.name: re.compile(p.pattern, re.IGNORECASE) for p in self.patterns}
    
    def scan_logs(
        self,
        lines: list[str],
        severity_filter: str = "all",
    ) -> list[Finding]:
        """
        Scan log lines for secret patterns.
        
        Args:
            lines: List of log lines (may include Docker timestamps)
            severity_filter: "all", "high", or "critical" to filter results
        
        Returns:
            List of Finding objects sorted by severity
        """
        findings = []
        
        # Severity filter levels
        severity_levels = {
            "critical": {"critical"},
            "high": {"critical", "high"},
            "all": {"critical", "high", "medium", "low"},
        }
        allowed_severities = severity_levels.get(severity_filter, severity_levels["all"])
        
        for line_num, line in enumerate(lines, start=1):
            # Extract timestamp if Docker format present (format: "YYYY-MM-DDTHH:MM:SS.sssssssZ <message>")
            timestamp = None
            message = line
            docker_ts_match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+(.*)", line)
            if docker_ts_match:
                timestamp, message = docker_ts_match.groups()
            
            # Scan each pattern
            for pattern_obj in self.patterns:
                if pattern_obj.severity not in allowed_severities:
                    continue
                
                compiled = self._compiled[pattern_obj.name]
                for match in compiled.finditer(message):
                    matched_text = match.group(0)
                    start, end = match.span()
                    
                    # Extract context (50 chars before/after)
                    context_before = message[max(0, start - 50):start]
                    context_after = message[end:min(len(message), end + 50)]
                    
                    # Redact the matched secret
                    if len(matched_text) > 6:
                        redacted = matched_text[:2] + "*" * (len(matched_text) - 4) + matched_text[-2:]
                    else:
                        redacted = "*" * len(matched_text)
                    
                    findings.append(
                        Finding(
                            severity=pattern_obj.severity,
                            pattern_name=pattern_obj.name,
                            line_number=line_num,
                            timestamp=timestamp,
                            context_before=context_before.strip(),
                            context_after=context_after.strip(),
                            matched_text_redacted=redacted,
                        )
                    )
        
        # Sort by severity (critical first) then line number
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings.sort(key=lambda f: (severity_order.get(f.severity, 4), f.line_number))
        
        return findings
    
    def get_findings_summary(self, findings: list[Finding]) -> dict:
        """Generate summary statistics from findings."""
        if not findings:
            return {
                "total_findings": 0,
                "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                "by_pattern": {},
            }
        
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        by_pattern = {}
        
        for finding in findings:
            by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
            by_pattern[finding.pattern_name] = by_pattern.get(finding.pattern_name, 0) + 1
        
        return {
            "total_findings": len(findings),
            "by_severity": by_severity,
            "by_pattern": by_pattern,
        }
    
    def get_recommendations(self, findings: list[Finding]) -> list[str]:
        """Generate remediation recommendations based on findings."""
        recommendations = []
        summary = self.get_findings_summary(findings)
        
        if summary["by_severity"].get("critical", 0) > 0:
            recommendations.append("🚨 CRITICAL: Rotate credentials immediately (found in logs)")
        
        if summary["by_pattern"].get("AWS Access Key ID", 0) > 0:
            recommendations.append("AWS credentials detected: rotate keys and check CloudTrail for abuse")
        
        if summary["by_pattern"].get("Private Key Header", 0) > 0:
            recommendations.append("Private key file content in logs: revoke key and regenerate")
        
        if summary["by_pattern"].get("Database URL with Credentials", 0) > 0:
            recommendations.append("Database credentials in logs: change password and review access logs")

        if summary["by_pattern"].get("Stripe Secret Key", 0) > 0:
            recommendations.append("Stripe secret key detected: rotate immediately at dashboard.stripe.com")

        if summary["by_pattern"].get("Google API Key", 0) > 0:
            recommendations.append("Google API key detected: revoke and regenerate in Google Cloud Console")

        if summary["by_pattern"].get("Azure Storage Account Key", 0) > 0:
            recommendations.append("Azure storage key detected: regenerate key in Azure Portal")

        if summary["by_severity"].get("high", 0) > 0 or summary["by_severity"].get("critical", 0) > 0:
            recommendations.append("Review logging configuration to prevent credential leakage")
            recommendations.append("Consider using environment variables or secret managers instead of logs")
        
        if summary["by_severity"].get("medium", 0) > 0:
            recommendations.append("PII/emails detected in logs: review GDPR/privacy compliance")
        
        return recommendations
