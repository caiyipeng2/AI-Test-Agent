# M3.1 Read-only Evidence Implementation Plan

> For agentic workers: use TDD in disjoint files. All implementation lives in the isolated M3 worktree; preserve the main checkout, private game checkout, installed package and accounts.

**Goal:** Validate portable state/ledger/PNG recordings without device input or false live-business claims.

**Architecture:** Separate schema2 parsing, evidence-pair/ledger assessment, readonly Unity publication and CLI. Preserve legacy schema1 and assess_upgrade. The approved first-loop scope and exact field contract are in the matching design spec.

**Tech Stack:** Python standard library/unittest; Unity JsonUtility and a synthetic Editor host.

## 1. Schema2 protocol

Files: create `src/ai_test_agent/bridge_v2.py`, `tests/test_bridge_v2.py` and shared `tests/m3_support.py`.

- [ ] Write tests for exact decimal retention; unavailable markers; duplicate/unknown JSON fields; monetary bounds; event gaps/duplicate transactions; source/sequence changes. Observe RED before implementation.
- [ ] Implement `parse_observation_line` and `ObservationStream` exactly as specified. Verify failed parsing does not mutate stream state.
- [ ] Run `python -m unittest discover -s tests -p test_bridge_v2.py -q`; require PASS, then run legacy `test_bridge.py`.

## 2. Evidence and ledger association

Files: create `src/ai_test_agent/m3_observation.py`, `tests/test_m3_observation.py`.

- [ ] Write failing tests for a reviewed upgrade with concurrent income, wrong debit, unknown balance, missing events, wrong transaction scope, PID/time/source/window mismatch, tampered/outside/reused PNG and high precision arithmetic.
- [ ] Implement `assess_recording(recording, *, evidence_root)`; derive non_upgrade_delta only from a complete validated ledger. Bind real files and call the existing joint assertion only after context validation.
- [ ] Run `python -m unittest discover -s tests -p test_m3_observation.py -q`; PASS means the readonly evidence contract works, not a live game upgrade.

## 3. Read-only Unity producer

Files: create `integrations/unity-test-bridge/Runtime/GameTestObservationPublisher.cs` and `Editor/ObservationCompileSmoke.cs`.

- [ ] Create a smoke entry that fails until the new producer exists, with synthetic expected schema/sequence/amount/event assertions.
- [ ] Implement DTOs and explicit readonly provider installation under the existing compilation guard. Do not add runtime control sockets or business mutations.
- [ ] Build in an isolated synthetic Unity Host, require `OBSERVATION_COMPILE_SMOKE_PASS`, then import the emitted line with the Python parser.

## 4. CLI and reproducible synthetic examples

Files: modify `src/ai_test_agent/__main__.py`; create `src/ai_test_agent/m3.py`, `examples/m3_recording_demo.py`, `tests/test_m3_cli.py`, `tests/test_m3_pipeline.py`.

- [ ] Test `assess-recording-m3 INPUT --output PATH.json`: PASS=0, FAIL=1, BLOCKED=2; all paths are device free. Reject unsafe output targets and retain explicit synthetic provenance.
- [ ] Generate tiny synthetic before/after PNGs and source-anchored recordings for pass, wrong-debit and missing-event scenarios in new output directories.
- [ ] Execute all three real file pipelines and rerun the PASS fixture to a distinct output; verify JSON/Markdown and original files retained.

## 5. Review and delivery

- [ ] Run full regression and compileall after focused checks pass. Review implementation and scope independently; fix concrete defects with regression tests.
- [ ] Add M3 usage, Mermaid flow, schema/adapter boundaries and sanitized verification report. Link from README/roadmap. Keep private identifiers, game source, packages and captures local.
- [ ] Commit, push `codex/m3-readonly-evidence`, and verify remote SHA. Do not merge main or change the real game until the applicable stage review is complete.
