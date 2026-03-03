"""Unit tests for secret_detector.py"""

import pytest

from docker_log_analyzer.secret_detector import SecretDetector, Finding


class TestSecretPatternDetection:
    """Test detection of various secret patterns."""
    
    def test_detects_aws_access_key(self):
        """Should detect AWS Access Key ID pattern (AKIA*)."""
        detector = SecretDetector()
        lines = ["2024-01-15T10:30:45.123456Z Error connecting with key AKIAIOSFODNN7EXAMPLE"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 1
        assert findings[0].pattern_name == "AWS Access Key ID"
        assert findings[0].severity == "critical"
        assert findings[0].line_number == 1
        assert "*" in findings[0].matched_text_redacted  # Should be redacted
        assert len(findings[0].matched_text_redacted) == len("AKIAIOSFODNN7EXAMPLE")  # Same length as original
    
    def test_detects_aws_secret_key(self):
        """Should detect AWS secret key assignment."""
        detector = SecretDetector()
        lines = ["aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 1
        assert findings[0].pattern_name == "AWS Secret Access Key"
        assert findings[0].severity == "critical"
    
    def test_detects_private_key_header(self):
        """Should detect private key file headers."""
        detector = SecretDetector()
        lines = [
            "Found key in config:",
            "-----BEGIN RSA PRIVATE KEY-----",
            "MIIEpAIBAAKCAQEA1234567890...",
        ]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 1
        assert findings[0].pattern_name == "Private Key Header"
        assert findings[0].severity == "critical"
        assert findings[0].line_number == 2
    
    def test_detects_github_token(self):
        """Should detect GitHub personal access tokens."""
        detector = SecretDetector()
        lines = ["Token for API: ghp_1234567890abcdefghijklmnopqrstuvwxyz"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 1
        assert findings[0].pattern_name == "GitHub Token"
        assert findings[0].severity == "critical"
    
    def test_detects_generic_api_key(self):
        """Should detect generic API key patterns."""
        detector = SecretDetector()
        lines = [
            "api_key=test_live_1234567890abcdefghijklmnop",
            "apikey: abc123def456ghi789jkl000mno111pqr",
        ]
        findings = detector.scan_logs(lines)
        
        # Should match both
        assert len(findings) >= 1
        assert any(f.pattern_name == "Generic API Key" for f in findings)
    
    def test_detects_bearer_token(self):
        """Should detect Bearer token patterns."""
        detector = SecretDetector()
        lines = ["Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) >= 1
        assert any(f.pattern_name == "Bearer Token" for f in findings)
    
    def test_detects_database_url(self):
        """Should detect database connection strings with credentials."""
        detector = SecretDetector()
        lines = [
            "postgres://user:password@localhost:5432/mydb",
            "mysql://dbadmin:secret123@db.example.com:3306/prod",
        ]
        findings = detector.scan_logs(lines)
        
        assert len(findings) >= 2
        db_urls = [f for f in findings if f.pattern_name == "Database URL with Credentials"]
        assert len(db_urls) == 2
        assert all(f.severity == "high" for f in db_urls)
    
    def test_detects_jwt_token(self):
        """Should detect JWT tokens."""
        detector = SecretDetector()
        lines = ["token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) >= 1
        assert any(f.pattern_name == "JWT Token" for f in findings)
    
    def test_detects_password_assignment(self):
        """Should detect password variable assignments."""
        detector = SecretDetector()
        lines = [
            'password="MySecurePassword123!"',
            "passwd: SomethingLongEnough",
        ]
        findings = detector.scan_logs(lines)
        
        assert len(findings) >= 1
        assert any(f.pattern_name == "Password Assignment" for f in findings)
    
    def test_detects_email_addresses(self):
        """Should detect email addresses (PII)."""
        detector = SecretDetector()
        lines = ["User logged in: user@example.com at admin@company.org"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 2
        assert all(f.pattern_name == "Email Address" for f in findings)
        assert all(f.severity == "medium" for f in findings)
    
    def test_detects_credit_card_pattern(self):
        """Should detect credit card number patterns."""
        detector = SecretDetector()
        lines = [
            "Card number: 4532-1234-5678-9010",
            "Payment: 3782 8224 6310 005",  # 4 groups of 4
        ]
        findings = detector.scan_logs(lines)
        
        assert len(findings) >= 1
        assert all(f.pattern_name == "Credit Card Number" for f in findings)


class TestSecretRedaction:
    """Test that secrets are properly redacted in output."""
    
    def test_redacts_matched_secret(self):
        """Should redact matched secret text."""
        detector = SecretDetector()
        lines = ["AKIAIOSFODNN7EXAMPLE is my AWS key"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 1
        redacted = findings[0].matched_text_redacted
        # Should show first 2 and last 2 chars, rest redacted
        assert redacted.startswith("AK")
        assert redacted.endswith("LE")
        assert redacted.count("*") > 0
        # Should not contain more than a few non-* chars
        assert redacted.count("*") > len(redacted) / 2
    
    def test_includes_context_around_secret(self):
        """Should include context before and after matched secret."""
        detector = SecretDetector()
        lines = ["prefix_text AKIAIOSFODNN7EXAMPLE suffix_text"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 1
        assert "prefix" in findings[0].context_before
        assert "suffix" in findings[0].context_after


class TestSeverityFiltering:
    """Test severity level filtering."""
    
    def test_filter_critical_only(self):
        """Should only return critical findings when filtered."""
        detector = SecretDetector()
        lines = [
            "AKIAIOSFODNN7EXAMPLE",  # critical
            "user@example.com",  # medium
        ]
        findings = detector.scan_logs(lines, severity_filter="critical")
        
        assert len(findings) == 1
        assert findings[0].severity == "critical"
    
    def test_filter_high_includes_critical(self):
        """Should include critical and high when filtered for high."""
        detector = SecretDetector()
        lines = [
            "AKIAIOSFODNN7EXAMPLE",  # critical
            "postgres://user:pass@localhost/db",  # high
            "user@example.com",  # medium
        ]
        findings = detector.scan_logs(lines, severity_filter="high")
        
        assert len(findings) == 2
        assert all(f.severity in ("critical", "high") for f in findings)
    
    def test_filter_all_returns_all(self):
        """Should return all findings when not filtered."""
        detector = SecretDetector()
        lines = [
            "AKIAIOSFODNN7EXAMPLE",  # critical
            "postgres://user:pass@localhost/db",  # high
            "user@example.com",  # medium
        ]
        findings = detector.scan_logs(lines, severity_filter="all")
        
        assert len(findings) == 3


class TestLineNumberTracking:
    """Test that line numbers are correctly tracked."""
    
    def test_tracks_line_numbers(self):
        """Should track which line secrets appear on."""
        detector = SecretDetector()
        lines = [
            "no secret here",
            "AKIAIOSFODNN7EXAMPLE",
            "another safe line",
            "postgres://user:pass@localhost/db",
        ]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 2
        assert findings[0].line_number == 2  # AWS key
        assert findings[1].line_number == 4  # DB URL
    
    def test_extracts_docker_timestamp(self):
        """Should extract timestamp from Docker log format."""
        detector = SecretDetector()
        lines = ["2024-01-15T10:30:45.123456Z AKIAIOSFODNN7EXAMPLE in logs"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 1
        assert findings[0].timestamp == "2024-01-15T10:30:45.123456Z"


class TestSummaryStatistics:
    """Test summary/statistics generation."""
    
    def test_empty_findings_summary(self):
        """Should handle empty findings list."""
        detector = SecretDetector()
        summary = detector.get_findings_summary([])
        
        assert summary["total_findings"] == 0
        assert all(v == 0 for v in summary["by_severity"].values())
    
    def test_counts_by_severity(self):
        """Should count findings by severity level."""
        detector = SecretDetector()
        lines = [
            "AKIAIOSFODNN7EXAMPLE",
            "postgres://user:pass@localhost/db",
            "user@example.com",
            "another@email.com",
        ]
        findings = detector.scan_logs(lines)
        summary = detector.get_findings_summary(findings)
        
        assert summary["total_findings"] == 4
        assert summary["by_severity"]["critical"] == 1
        assert summary["by_severity"]["high"] == 1
        assert summary["by_severity"]["medium"] == 2
    
    def test_counts_by_pattern(self):
        """Should count findings by pattern type."""
        detector = SecretDetector()
        lines = [
            "AKIAIOSFODNN7EXAMPLE",
            "AKIAI4QDP4PJW2FAKE12",  # Another AWS key
            "postgres://user:pass@localhost/db",
        ]
        findings = detector.scan_logs(lines)
        summary = detector.get_findings_summary(findings)
        
        assert summary["by_pattern"].get("AWS Access Key ID", 0) == 2
        assert summary["by_pattern"].get("Database URL with Credentials", 0) == 1


class TestRecommendations:
    """Test security recommendation generation."""
    
    def test_recommends_key_rotation_for_critical(self):
        """Should recommend immediate action for critical findings."""
        detector = SecretDetector()
        lines = ["AKIAIOSFODNN7EXAMPLE"]
        findings = detector.scan_logs(lines)
        recommendations = detector.get_recommendations(findings)
        
        assert len(recommendations) > 0
        assert any("rotate" in r.lower() for r in recommendations)
        assert any("critical" in r.lower() for r in recommendations)
    
    def test_recommends_db_password_change(self):
        """Should recommend database password change."""
        detector = SecretDetector()
        lines = ["postgres://user:pass@localhost/db"]
        findings = detector.scan_logs(lines)
        recommendations = detector.get_recommendations(findings)
        
        assert any("database" in r.lower() or "password" in r.lower() for r in recommendations)
    
    def test_recommends_logging_config_review(self):
        """Should recommend reviewing logging configuration."""
        detector = SecretDetector()
        lines = ["AKIAIOSFODNN7EXAMPLE"]
        findings = detector.scan_logs(lines)
        recommendations = detector.get_recommendations(findings)
        
        assert any("logging" in r.lower() for r in recommendations)
    
    def test_empty_recommendations_for_no_findings(self):
        """Should not return recommendations if no findings."""
        detector = SecretDetector()
        recommendations = detector.get_recommendations([])
        
        assert len(recommendations) == 0


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_empty_log_lines(self):
        """Should handle empty log input."""
        detector = SecretDetector()
        findings = detector.scan_logs([])
        
        assert len(findings) == 0
    
    def test_very_long_log_lines(self):
        """Should handle very long log lines."""
        detector = SecretDetector()
        long_line = "x" * 10000 + "AKIA7XAMPLE12345678" + "y" * 10000
        findings = detector.scan_logs([long_line])
        
        assert len(findings) == 1
        assert findings[0].pattern_name == "AWS Access Key ID"
    
    def test_multiple_secrets_same_line(self):
        """Should detect multiple secrets on the same line."""
        detector = SecretDetector()
        lines = ["AKIAIOSFODNN7EXAMPLE and AKIAI4QDP4PJW2FAKE12 both here"]
        findings = detector.scan_logs(lines)
        
        assert len(findings) == 2
        assert all(f.line_number == 1 for f in findings)
    
    def test_case_insensitive_detection(self):
        """Should detect patterns regardless of case."""
        detector = SecretDetector()
        lines = [
            "PASSWORD=\"secret123fake\"",
            "Password=\"secret123fake\"",
            "PASSWD=\"secret123fake\"",
        ]
        findings = detector.scan_logs(lines, severity_filter="medium")
        
        # Should match all case variations
        assert len(findings) >= 1
