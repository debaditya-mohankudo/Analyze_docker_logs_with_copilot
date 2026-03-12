# Documentation Wiki Home

Canonical entry point for all Docker Log Analyzer documentation. Organized by intent — each section points to one source of truth.

---

## Agent Routing (Copilot / Claude)

Use this table to answer questions with minimal hops.

| User intent | Open first | Then open | Canonical answer |
|---|---|---|---|
| "How does X work architecturally?" | [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md) | [../CLAUDE.md](../CLAUDE.md) | [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md) |
| "What does tool X do / what are its params?" | [WIKI_TOOLS.md](WIKI_TOOLS.md) | — | [WIKI_TOOLS.md](WIKI_TOOLS.md) |
| "How do I set up / run this?" | [WIKI_OPERATIONS.md § Quick Start](WIKI_OPERATIONS.md#quick-start) | [../README.md](../README.md) | [WIKI_OPERATIONS.md § Quick Start](WIKI_OPERATIONS.md#quick-start) |
| "What Copilot prompts can I use?" | [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md) | — | [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md) |
| "How does caching work?" | [WIKI_OPERATIONS.md § Log Cache](WIKI_OPERATIONS.md#log-cache-strategy) | [WIKI_ARCHITECTURE.md § Cache](WIKI_ARCHITECTURE.md#cache-system) | [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md) |
| "How is this tested? What's CI?" | [WIKI_QUALITY.md](WIKI_QUALITY.md) | — | [WIKI_QUALITY.md](WIKI_QUALITY.md) |
| "What Copilot prompts can I use for triage?" | [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md) | — | [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md) |
| "What are the architecture rules / constraints?" | [../CLAUDE.md](../CLAUDE.md) | [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md) | [../CLAUDE.md](../CLAUDE.md) |
| "How does dependency mapping work?" | [WIKI_TOOLS.md § map_service_dependencies](WIKI_TOOLS.md#10-map_service_dependencies) | [WIKI_ARCHITECTURE.md § Dependency Mapping](WIKI_ARCHITECTURE.md#dependency-mapping) | [WIKI_TOOLS.md](WIKI_TOOLS.md) |
| "How do I create an investigation plan?" | [WIKI_INVESTIGATION_PLANNER.md](WIKI_INVESTIGATION_PLANNER.md) | [WIKI_TOOLS.md § plan_investigation](WIKI_TOOLS.md#13-plan_investigation) | [WIKI_INVESTIGATION_PLANNER.md](WIKI_INVESTIGATION_PLANNER.md) |
| "How do I see the code around a container error?" | [WIKI_CODE_REPO.md](WIKI_CODE_REPO.md) | [WIKI_TOOLS.md § analyze_code_context](WIKI_TOOLS.md#14-analyze_code_context) | [WIKI_CODE_REPO.md](WIKI_CODE_REPO.md) |
| "How does secret detection / redaction work?" | [WIKI_SECURITY.md](WIKI_SECURITY.md) | — | [WIKI_SECURITY.md](WIKI_SECURITY.md) |
| "What are the path confinement / security guardrails?" | [WIKI_SECURITY.md](WIKI_SECURITY.md) | — | [WIKI_SECURITY.md](WIKI_SECURITY.md) |
| "How do I add a secret pattern?" | [WIKI_SECURITY.md § Adding a new secret pattern](WIKI_SECURITY.md#adding-a-new-secret-pattern) | — | [WIKI_SECURITY.md](WIKI_SECURITY.md) |

---

## Canonical Sources (Single Source of Truth)

| Topic | Owner |
|-------|-------|
| MCP tool contracts (params, returns, behavior) | [WIKI_TOOLS.md](WIKI_TOOLS.md) |
| Copilot prompts and workflows | [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md) |
| Architecture rules and contributor constraints | [../CLAUDE.md](../CLAUDE.md) |
| Module design, algorithms, signal confidence | [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md) |
| Setup, config, cache, remote Docker | [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md) |
| Test strategy, CI, coverage targets | [WIKI_QUALITY.md](WIKI_QUALITY.md) |
| Security guardrails (secret detection, path confinement) | [WIKI_SECURITY.md](WIKI_SECURITY.md) |

---

## Start Here

- **New user:** [WIKI_OPERATIONS.md § Quick Start](WIKI_OPERATIONS.md#quick-start) → [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md)
- **New contributor:** [WIKI_OPERATIONS.md § Quick Start](WIKI_OPERATIONS.md#quick-start) → [WIKI_TOOLS.md](WIKI_TOOLS.md)
- **Using with Copilot:** [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md)
- **Understanding all tools:** [WIKI_TOOLS.md](WIKI_TOOLS.md)
- **Adding a new tool:** [WIKI_ARCHITECTURE.md § Adding New Tools](WIKI_ARCHITECTURE.md#adding-new-tools)
- **Architecture constraints:** [../CLAUDE.md](../CLAUDE.md)

---

## Wiki Hubs

| Hub | Purpose |
|-----|---------|
| [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md) | System design, modules, algorithms, confidence model |
| [WIKI_TOOLS.md](WIKI_TOOLS.md) | All 14 MCP tools — parameters, return shapes, behavior |
| [WIKI_INVESTIGATION_PLANNER.md](WIKI_INVESTIGATION_PLANNER.md) | Investigation planner — signal detection, plan steps, file output |
| [WIKI_CODE_REPO.md](WIKI_CODE_REPO.md) | Code repo context — stack trace parsing, file resolution, code deep-dive |
| [WIKI_COPILOT_PROMPTS.md](WIKI_COPILOT_PROMPTS.md) | Natural language prompts organized by workflow |
| [WIKI_OPERATIONS.md](WIKI_OPERATIONS.md) | Setup, configuration, cache, remote Docker |
| [WIKI_QUALITY.md](WIKI_QUALITY.md) | Test suite, CI, coverage, adding tests |
| [WIKI_SECURITY.md](WIKI_SECURITY.md) | Secret detection, redaction rules, path confinement guardrails |

## Proposals

| Proposal                                                                       | Status      | Purpose                                                                              |
|--------------------------------------------------------------------------------|-------------|--------------------------------------------------------------------------------------|
| [WIKI_PROPOSAL_ROOT_CAUSE_ANALYZER.md](WIKI_PROPOSAL_ROOT_CAUSE_ANALYZER.md)   | IN PROGRESS | Rank containers by root-cause likelihood (tool #11); Issues A+B done, C/D/E pending  |

## Code Reviews

| Review                                                                   | Date       | Module                   | Open issues                      |
| ------------------------------------------------------------------------ | ---------- | ------------------------ | -------------------------------- |
| [WIKI_REVIEW_DEPENDENCY_MAPPER.md](WIKI_REVIEW_DEPENDENCY_MAPPER.md)     | 2026-03-07 | `dependency_mapper.py`   | 3 open (Mermaid, HTTP-from, k8s) |
| [WIKI_REVIEW_ROOT_CAUSE_ANALYZER.md](WIKI_REVIEW_ROOT_CAUSE_ANALYZER.md) | 2026-03-07 | `root_cause_analyzer.py` | 2 open (Issues F, G)             |

---

## Authoring Rules

- One page is source-of-truth per topic. Prefer linking over duplicating.
- Every page must have a **See also** section and **Retrieval keywords** block.
- Update this home page when adding a new hub or canonical source.

---

## Fast Paths

**Retrieval keywords:** docker, log, analyzer, MCP, tool, container, copilot, agent, spike, correlation, dependency, secret, cache, pattern, wiki, navigation, index, home, entry, start, routing

**[negative keywords / not-this-doc]**
LLM, Kafka, OpenAI, external API, algorithm detail, test count, CI config, coverage number
