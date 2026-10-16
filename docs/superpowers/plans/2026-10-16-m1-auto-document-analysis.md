# M1 Automatic Document Analysis Implementation Plan

> **For agentic workers:** Use subagent-driven-development with disjoint file ownership. Do not modify the game project, invoke Android tools, commit private source documents or create human approvals.

**Goal:** One command reads a real document, invokes a configured model, validates source-linked business analysis, and exports drafts for human review.

**Architecture:** A local extractor supplies normalized source and image attachments. Provider-neutral semantic output is compiled into existing M1 contracts. CLI and OpenAI-compatible HTTP adapters share the output schema and fail explicitly.

**Tech Stack:** Python standard library, unittest, DOCX/XLSX ZIP/XML parsing, Codex CLI, configurable Chat Completions-compatible HTTP.

## Task 1: Document extraction

- Create `src/ai_test_agent/documents.py`, `tests/test_documents.py`.
- Write synthetic DOCX/XLSX ZIP fixtures in tests, including deleted text, tables, image relationships, wrong worksheet dimension, and path traversal/oversized archives. Confirm failures before implementing.
- Implement `extract_document(path, output_dir, allow_model_access=False)` using bounded ZIP/XML reads. Keep original file unchanged and produce deterministic anchors and image manifest.
- Run `python -m unittest tests.test_documents -v`.

## Task 2: Model output protocol

- Create `src/ai_test_agent/analysis_protocol.py`, `tests/test_analysis_protocol.py`.
- Test valid business output, dangling source references, ignored sections/images, unknown fields, invented approvals and missing independent assertions before implementation.
- Implement strict JSON schema, prompt and compiler that registers all results as DRAFT and calls existing `validate_result`.
- Run `python -m unittest tests.test_analysis_protocol -v`.

## Task 3: Model providers

- Create `src/ai_test_agent/model_providers.py`, `tests/test_model_providers.py` and synthetic provider config examples.
- Test CLI argument-list invocation, timeout/failure classification and JSON handling. Verify HTTP request against a local fixture server, including model, auth environment, schema, image payloads, refusal and truncation handling.
- Implement configurable Codex CLI and OpenAI-compatible HTTP adapters; never use shell-built commands or log credentials.
- Run `python -m unittest tests.test_model_providers -v`.

## Task 4: Pipeline and commands

- Create `src/ai_test_agent/document_analysis.py`, `tests/test_document_analysis.py`; extend `__main__.py` and CLI tests.
- Test a new raw document with a synthetic model transport, no hand-authored M1 analysis import. Verify exports, citations, human-review queue, failures and BLOCKED M0 handoff.
- Implement extract-only and analyze commands. Write run status even if provider fails and never create false success outputs.
- Run focused tests, then M1/M0 regression and compileall.

## Task 5: Real execution and acceptance

- Invoke the new command on the user-provided DOCX via the actual configured Codex CLI; retain commands and trace locally.
- Inspect rule conflicts, image/table ingestion and source coverage. Label remaining unknowns and ensure no human approval or game execution is fabricated.
- Update M1 exit criteria, roadmap, README and sanitized verification report. Commit, then push the feature branch. Wait for user acceptance before main merge.
