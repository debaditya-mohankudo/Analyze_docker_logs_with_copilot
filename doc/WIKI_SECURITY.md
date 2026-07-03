---
tags: [security, secret, credential, redact, pii, path-traversal, confinement]
last_updated: 2026-07-03
---

# Wiki Hub: Security

Canonical reference for all security constraints, guardrails, and patterns in Docker Log Analyzer.

---

## Agent Use Rules

- Start here for "what secrets are detected", "how does redaction work", "what are the path safety rules", "how do I add a secret pattern".
- Architecture rules are in [../CLAUDE.md §6](../CLAUDE.md) (stub that points here).
- Tool parameters for `detect_data_leaks` are in [WIKI_TOOLS.md § detect_data_leaks](WIKI_TOOLS.md#4-detect_data_leaks).

---

## 1. Secret Detection (`secret_detector.py`)

### What it does

`SecretDetector` scans log lines against 20 compiled regex patterns and returns
structured findings with redacted values. It never echoes the full matched secret.

### Output contract

Every finding contains:

| Field | Type | Description |
|-------|------|-------------|
| `severity` | `critical` \| `high` \| `medium` | Classified by pattern |
| `pattern_name` | string | Named pattern that fired (e.g. `AWS_SECRET_KEY`) |
| `line_number` | int | 1-based line number within the scanned log |
| `timestamp` | string? | Extracted from the log line if available, else `null` |
| `context_before` | string | Log content immediately preceding the match, for triage context |
| `context_after` | string | Log content immediately following the match |
| `matched_text_redacted` | string | **Redacted** form only — raw secret value never returned |
| `recommendation` | string | Remediation suggestion, populated from the pattern's own recommendation at scan time |

(Backed by the `Finding` dataclass in `secret_detector.py` — there is no
separate `matched_text` field; only the already-redacted value is ever
constructed or returned.)

### Rules (CRITICAL)

- **Must redact** before returning — raw credential values must never appear in tool output.
- **Must categorize severity** — every pattern is assigned `critical`, `high`, or `medium` at definition time.
- **Must include remediation** — every finding must include a `recommendation` string.
- **Never log secrets** in raw form — not to stdout, not to the logger, not to any file.

### Severity levels and patterns (20 total)

Pattern names below are the exact `pattern_name` values returned in findings
(the human-readable `name=` in each `SecretPattern`, per `secret_detector.py`).

| Pattern | Severity |
|---------|----------|
| `AWS Access Key ID`, `AWS Secret Access Key` | critical |
| `Private Key Header` | critical |
| `GitHub Token` | critical |
| `Stripe Secret Key` | critical |
| `Generic API Key`, `Bearer Token`, `Database URL with Credentials`, `Slack Token`, `JWT Token` | high |
| `Google API Key`, `Stripe Publishable Key`, `Azure Storage Account Key`, `OAuth Client Secret` | high |
| `Password Assignment`, `Email Address`, `Credit Card Number` | medium |
| `Secret Assignment`, `Base64 Encoded Secret`, `Session Cookie` | medium |

There is **no SSN or phone-number pattern** — despite PII detection being
mentioned in the module's own docstring, only email and credit-card patterns
are currently implemented for PII.

**Severity filter behavior:** only `critical`, `high`, and `all` are recognized
filter values (`secret_detector.scan_logs`) — `critical` returns only critical
findings, `high` returns critical + high, `all` returns everything (critical +
high + medium). There is no `"medium"` filter key; passing `"medium"` or any
other unrecognized string silently falls back to `all` behavior rather than
raising an error.

### Adding a new secret pattern

1. Add the compiled regex to `secret_detector.py` with an explicit severity.
2. Add a unit test to `tests/test_secret_detector.py` with a synthetic match line.
3. Verify no false positives on sample logs before merging.
4. Update the pattern table above.

---

## 2. Repository Path Confinement (`coderepo.py`)

### What it does

`find_file_in_repo` resolves stack-frame file paths from container logs to absolute
paths on the host. Container logs can contain attacker-controlled or unexpected
absolute paths (e.g. `/etc/passwd`, `/home/user/.ssh/id_rsa`). Without confinement
enforcement, those paths would be read and returned to the caller.

### Rules (CRITICAL)

#### Rule 1 — Containment before existence check

Before accepting any **absolute** frame path, resolve both `repo_root` and the
candidate to absolute paths, then confirm the candidate is under `repo_root`:

```python
resolved_root = repo_root.resolve()
resolved_candidate = candidate.resolve(strict=False)

if resolved_candidate.is_relative_to(resolved_root) and resolved_candidate.is_file():
    return resolved_candidate
```

Never call `candidate.is_file()` before the containment check passes.

#### Rule 1a — Always resolve `repo_root` before comparing

`Path.is_relative_to()` (and the older `relative_to()`) requires both operands
to share a common absolute prefix. When `REPO_PATHS` is configured as `.`,
`repo_root` is a relative path and the containment check silently fails for
every absolute frame path — including legitimate ones inside the repo —
breaking code-context extraction for the common `REPO_PATHS=.` config.

`repo_root.resolve()` converts the configured path to its absolute host
equivalent before any comparison. `candidate.resolve(strict=False)` normalises
the frame path (handles `..`, symlinks) without raising if the file is absent.
The same `resolved_root` is used for the re-rooting branch (Rule 3).

#### Rule 2 — Early return after the absolute block

After the absolute-path block, always `return None` on no-match. Do **not** fall
through to the relative-path logic.

**Why:** Python's `/` operator discards the left side when the right operand is absolute:

```python
Path("/repo") / "/etc/passwd"   # → Path("/etc/passwd")  ← not confined!
```

If an absolute frame path reaches the relative section, `repo_root / frame_path`
silently becomes the raw host path, bypassing all confinement.

#### Rule 3 — Re-rooting is the only permitted absolute→host mapping

The re-rooting branch (strip the leading anchor, prepend `repo_root`) is the only
permitted way to convert an absolute container path to a host file:

```python
rel = resolved_candidate.relative_to(resolved_candidate.anchor)   # strip leading "/"
rooted = resolved_root / rel                                        # always under repo_root
```

This is safe because the join starts from `resolved_root`. Any absolute path is
relative to its own anchor, so `relative_to(anchor)` never raises `ValueError`.

### Required test for every change to `find_file_in_repo`

Every PR that touches `find_file_in_repo` must include a test that passes an absolute
path **outside** `repo_root` and asserts `None` is returned:

```python
def test_absolute_path_outside_repo_is_rejected(self, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("sensitive")
    result = find_file_in_repo(repo, str(outside))
    assert result is None
```

This test lives in `tests/test_coderepo.py :: TestFindFileInRepo`.

---

## 3. Docker Socket Access

- The Docker socket is mounted **read-only**.
- Tools must never attempt container modification (exec, kill, stop, write).
- `start_test_containers` / `stop_test_containers` are the only allowed lifecycle
  tools, and only for test containers defined in `docker-compose.test.yml`.

---

## 4. General Output Safety

| Rule | Location |
|------|----------|
| Never print to stdout except MCP protocol frames | All `tool_*()` functions |
| Structured error JSON — never raise through the MCP boundary | All `tool_*()` functions |
| No raw secrets in logger output | `secret_detector.py`, `tools.py` |
| No network calls except Docker daemon | Entire codebase |
| No telemetry, no external SaaS | Entire codebase |

---

## Retrieval keywords

security, secret, credential, redact, PII, path traversal, repo root, confinement,
find_file_in_repo, SecretDetector, detect_data_leaks, AWS, GitHub token, credit card,
private key, JWT, severity, critical, high, medium, Docker socket, read-only,
safe output, no stdout

**[negative keywords / not-this-doc]**
test strategy, CI, coverage, architecture, tool params, cache strategy, Copilot prompts

---

## See also

- Tool parameters for `detect_data_leaks`: [WIKI_TOOLS.md § detect_data_leaks](WIKI_TOOLS.md#4-detect_data_leaks)
- Architecture constraints: [../CLAUDE.md](../CLAUDE.md)
- Test strategy: [WIKI_QUALITY.md](WIKI_QUALITY.md)
- Home: [WIKI_HOME.md](WIKI_HOME.md)
