# M2 Deterministic Android Smoke Implementation Plan

> **For agentic workers:** Work in disjoint files using TDD. Preserve the game checkout and device data; do not install or launch packages from worker tasks. Main agent owns real device actions and Git writes.

**Goal:** Build a reusable artifact/device/install/startup/readiness/evidence loop with explicit environment proof and M0 traceability.

**Architecture:** Reuse AndroidDevice and M0Registry. Separate archive verification, observation rules and orchestration, so project-specific values live in contracts. Every attempt has fresh log boundaries, bounded commands and immutable evidence directories.

**Tech Stack:** Python standard library, unittest, Android ADB and existing bundletool/Java.

## Task 1: Artifact verification

- Create `src/ai_test_agent/m2_artifacts.py`, `tests/test_m2_artifacts.py`.
- Write synthetic APKS/base APK tests: manifest metadata, hash, test/prod asset policy, unsafe archive names, missing/duplicate base, large asset and mismatched fields. Observe RED before implementation.
- Implement strict manifest/asset verification and persist observed evidence before a mismatch returns BLOCKED.

## Task 2: Android observations

- Extend `src/ai_test_agent/android.py`; create `tests/test_android_m2.py`.
- Test explicit device selection/status, device-clock log anchor and target-only force-stop. Existing methods remain backward compatible.
- Create `m2_observation.py`, `tests/test_m2_observation.py` for current-window/package/PID crash and ANR classification plus explicit readiness signals.

## Task 3: M2 runner

- Create `src/ai_test_agent/m2.py`, `tests/test_m2.py` with injected device, executor and clock.
- Test formal/unknown preflight blocks all mutations, preservation of data, installation failure and post-check mismatch, late-ready success, timeout, PID change, foreground mismatch and corrupt evidence.
- Implement remaining-deadline command budget, deterministic polling, unique run/attempt, config fingerprint and command/raw-output/PNG manifest.

## Task 4: CLI and traceability

- Add `m2-smoke` and `m2-report` commands; create generic contract example and M2 documentation.
- Add `m2_trace.py` and tests for M0 source→requirement→case→build→run→evidence linkage and checksum validation.
- Keep business assertions and M1 approval outside the M2 success claim.

## Task 5: Verification and delivery

- Run all new tests, then full regression and compileall.
- Use the selected Samsung and a verified test build only. Record real formal-package and downgrade blockers; if a suitable test package arrives, perform installation/startup and same-contract rerun.
- Inspect actual output and evidence, perform spec/code review, update roadmap and write sanitized verification report.
- Commit implementation and docs, then push feature branch. Wait for user acceptance before merge or M3 work.
