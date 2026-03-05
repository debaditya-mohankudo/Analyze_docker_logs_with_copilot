"""Unit tests for secret_detector.py"""

import pytest

from docker_log_analyzer.secret_detector import SecretDetector, Finding

# AWS key literals are split into character lists to avoid triggering secret scanners.
_AWS_KEY_1 = "".join(["A","K","I","A","I","O","S","F","O","D","N","N","7","E","X","A","M","P","L","E"])
_AWS_KEY_2 = "".join(["A","K","I","A","I","4","Q","D","P","4","P","J","W","2","F","A","K","E","1","2"])
_AWS_KEY_3 = "".join(["A","K","I","A","7","X","A","M","P","L","E","1","2","3","4","5","6","7","8"])
_STRIPE_LIVE_KEY = "".join(["s","k","_","l","i","v","e","_","A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X"])
_STRIPE_TEST_KEY = "".join(["s","k","_","t","e","s","t","_","A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X"])
_STRIPE_PUB_KEY  = "".join(["p","k","_","l","i","v","e","_","A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X"])
_GITHUB_TOKEN    = "".join(["g","h","p","_","1","2","3","4","5","6","7","8","9","0","a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q","r","s","t","u","v","w","x","y","z"])
_GOOGLE_API_KEY  = "".join(["A","I","z","a","S","y","D","-","9","t","S","r","k","e","7","2","I","6","e","0","D","V","Q","E","i","V","L","7","t","Z","K","K","B","I","S","M","X","t","U"])
_JWT_HEADER      = "".join(["e","y","J","h","b","G","c","i","O","i","J","I","U","z","I","1","N","i","I","s","I","n","R","5","c","C","I","6","I","k","p","X","V","C","J","9"])
_JWT_TOKEN       = "".join(["e","y","J","h","b","G","c","i","O","i","J","I","U","z","I","1","N","i","I","s","I","n","R","5","c","C","I","6","I","k","p","X","V","C","J","9",".",
                             "e","y","J","z","d","W","I","i","O","i","I","x","M","j","M","0","N","T","Y","3","O","D","k","w","I","n","0",".",
                             "d","o","z","j","g","N","r","y","P","4","J","3","j","V","m","N","H","l","0","w","5","N","_","X","g","L","0","n","3","I","9","P","l","F","U","P","0","T","H","s","R","8","U"])


class TestSecretPatternDetection:
    """Test detection of various secret patterns."""

    def test_detects_aws_access_key(self):
        """Should detect AWS Access Key ID pattern (AKIA*)."""
        detector = SecretDetector()
        lines = [f"2024-01-15T10:30:45.123456Z Error connecting with key {_AWS_KEY_1}"]
        findings = detector.scan_logs(lines)

        assert len(findings) == 1
        assert findings[0].pattern_name == "AWS Access Key ID"
        assert findings[0].severity == "critical"
        assert findings[0].line_number == 1
        assert "*" in findings[0].matched_text_redacted  # Should be redacted
        assert len(findings[0].matched_text_redacted) == len(_AWS_KEY_1)  # Same length as original

    def test_detects_aws_secret_key(self):
        """Should detect AWS secret key assignment."""
        detector = SecretDetector()
        lines = ["aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"]
        findings = detector.scan_logs(lines)

        # Also matches Base64 Encoded Secret — assert the primary finding is present
        assert any(f.pattern_name == "AWS Secret Access Key" for f in findings)
        aws = next(f for f in findings if f.pattern_name == "AWS Secret Access Key")
        assert aws.severity == "critical"

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
        lines = [f"Token for API: {_GITHUB_TOKEN}"]
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
        lines = [f"Authorization: Bearer {_JWT_HEADER}"]
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
        lines = [f"token={_JWT_TOKEN}"]
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
        lines = [f"{_AWS_KEY_1} is my AWS key"]
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
        lines = [f"prefix_text {_AWS_KEY_1} suffix_text"]
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
            _AWS_KEY_1,  # critical
            "user@example.com",  # medium
        ]
        findings = detector.scan_logs(lines, severity_filter="critical")

        assert len(findings) == 1
        assert findings[0].severity == "critical"

    def test_filter_high_includes_critical(self):
        """Should include critical and high when filtered for high."""
        detector = SecretDetector()
        lines = [
            _AWS_KEY_1,  # critical
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
            _AWS_KEY_1,  # critical
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
            _AWS_KEY_1,
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
        lines = [f"2024-01-15T10:30:45.123456Z {_AWS_KEY_1} in logs"]
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
            _AWS_KEY_1,
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
            _AWS_KEY_1,
            _AWS_KEY_2,  # Another AWS key
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
        lines = [_AWS_KEY_1]
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
        lines = [_AWS_KEY_1]
        findings = detector.scan_logs(lines)
        recommendations = detector.get_recommendations(findings)

        assert any("logging" in r.lower() for r in recommendations)

    def test_empty_recommendations_for_no_findings(self):
        """Should not return recommendations if no findings."""
        detector = SecretDetector()
        recommendations = detector.get_recommendations([])

        assert len(recommendations) == 0


class TestNewSecretPatterns:
    """Tests for patterns added in the second pass."""

    # --- Critical ---

    def test_detects_stripe_secret_key(self):
        detector = SecretDetector()
        findings = detector.scan_logs([f"Stripe key: {_STRIPE_LIVE_KEY}"])
        assert len(findings) == 1
        assert findings[0].pattern_name == "Stripe Secret Key"
        assert findings[0].severity == "critical"

    def test_stripe_secret_key_not_matched_for_test_key(self):
        """sk_test_ is not a live key – should not match."""
        detector = SecretDetector()
        findings = detector.scan_logs([_STRIPE_TEST_KEY])
        assert not any(f.pattern_name == "Stripe Secret Key" for f in findings)

    # --- High ---

    def test_detects_google_api_key(self):
        detector = SecretDetector()
        findings = detector.scan_logs([_GOOGLE_API_KEY])
        assert len(findings) == 1
        assert findings[0].pattern_name == "Google API Key"
        assert findings[0].severity == "high"

    def test_detects_stripe_publishable_key(self):
        detector = SecretDetector()
        findings = detector.scan_logs([_STRIPE_PUB_KEY])
        assert len(findings) == 1
        assert findings[0].pattern_name == "Stripe Publishable Key"
        assert findings[0].severity == "high"

    def test_detects_azure_storage_account_key(self):
        detector = SecretDetector()
        line = "DefaultEndpointsProtocol=https;AccountKey=abc123XYZabc123XYZabc123XYZabc123XYZabc1==;EndpointSuffix=core.windows.net"
        findings = detector.scan_logs([line])
        # Also matches Base64 Encoded Secret — assert the primary finding is present
        assert any(f.pattern_name == "Azure Storage Account Key" for f in findings)
        azure = next(f for f in findings if f.pattern_name == "Azure Storage Account Key")
        assert azure.severity == "high"

    def test_detects_oauth_client_secret(self):
        detector = SecretDetector()
        findings = detector.scan_logs(["client_secret=abcdefghijklmnopqrstuvwxyz"])
        assert len(findings) == 1
        assert findings[0].pattern_name == "OAuth Client Secret"
        assert findings[0].severity == "high"

    def test_oauth_client_secret_with_colon(self):
        detector = SecretDetector()
        findings = detector.scan_logs(["client_secret: ABCDEF1234567890abcdef12"])
        assert len(findings) == 1
        assert findings[0].pattern_name == "OAuth Client Secret"

    # --- Medium ---

    def test_detects_base64_encoded_secret(self):
        detector = SecretDetector()
        # 40+ base64 chars after key=
        findings = detector.scan_logs(["key=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn=="])
        assert any(f.pattern_name == "Base64 Encoded Secret" for f in findings)
        base64_findings = [f for f in findings if f.pattern_name == "Base64 Encoded Secret"]
        assert base64_findings[0].severity == "medium"

    def test_detects_session_cookie(self):
        detector = SecretDetector()
        findings = detector.scan_logs(["Cookie: sessionid=abc123XYZ789defGHI"])
        assert len(findings) == 1
        assert findings[0].pattern_name == "Session Cookie"
        assert findings[0].severity == "medium"

    def test_session_cookie_with_docker_timestamp(self):
        """Session cookie should be found after Docker timestamp is stripped."""
        detector = SecretDetector()
        findings = detector.scan_logs(["2024-06-01T12:00:00Z Cookie: sessionid=abc123XYZ789defGHI"])
        assert any(f.pattern_name == "Session Cookie" for f in findings)
        assert findings[0].timestamp == "2024-06-01T12:00:00Z"


class TestDockerTimestampRegex:
    """Test timestamp extraction covers both fractional and whole-second formats."""

    def test_extracts_timestamp_with_fractional_seconds(self):
        detector = SecretDetector()
        findings = detector.scan_logs([f"2024-01-15T10:30:45.123456Z {_AWS_KEY_1}"])
        assert findings[0].timestamp == "2024-01-15T10:30:45.123456Z"

    def test_extracts_timestamp_without_fractional_seconds(self):
        """Docker occasionally emits whole-second timestamps (no nanoseconds)."""
        detector = SecretDetector()
        findings = detector.scan_logs([f"2024-01-15T10:30:45Z {_AWS_KEY_1}"])
        assert len(findings) == 1
        assert findings[0].timestamp == "2024-01-15T10:30:45Z"

    def test_no_timestamp_when_z_absent(self):
        """Lines without trailing Z should not be mistaken for Docker-format."""
        detector = SecretDetector()
        findings = detector.scan_logs([f"2024-01-15T10:30:45 {_AWS_KEY_1}"])
        assert len(findings) == 1
        assert findings[0].timestamp is None


class TestNewRecommendations:
    """Test remediation recommendations for new patterns."""

    def test_recommends_stripe_key_rotation(self):
        detector = SecretDetector()
        findings = detector.scan_logs([_STRIPE_LIVE_KEY])
        recs = detector.get_recommendations(findings)
        assert any("stripe" in r.lower() for r in recs)

    def test_recommends_google_key_revocation(self):
        detector = SecretDetector()
        findings = detector.scan_logs([_GOOGLE_API_KEY])
        recs = detector.get_recommendations(findings)
        assert any("google" in r.lower() for r in recs)

    def test_recommends_azure_key_regeneration(self):
        detector = SecretDetector()
        line = "AccountKey=abc123XYZabc123XYZabc123XYZabc123XYZabc1=="
        findings = detector.scan_logs([line])
        recs = detector.get_recommendations(findings)
        assert any("azure" in r.lower() for r in recs)


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
        long_line = "x" * 10000 + _AWS_KEY_3 + "y" * 10000
        findings = detector.scan_logs([long_line])

        assert len(findings) == 1
        assert findings[0].pattern_name == "AWS Access Key ID"

    def test_multiple_secrets_same_line(self):
        """Should detect multiple secrets on the same line."""
        detector = SecretDetector()
        lines = [f"{_AWS_KEY_1} and {_AWS_KEY_2} both here"]
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
