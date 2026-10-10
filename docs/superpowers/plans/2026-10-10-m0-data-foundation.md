# M0 Data Foundation Implementation Plan

> **For agentic workers:** M0 only. Do not execute Android actions, install packages, call model APIs, or modify the game project until this plan is accepted by the M0 gate.

**Goal:** Create a device-free, machine-validated registry that binds approved sources, requirements, risk/testpoints, cases, builds, runs/attempts, evidence and defects into one traceable report.

**Architecture:** `M0Registry` owns an append-oriented JSON document with stable IDs and cross-record validation. The CLI validates the registry and exports coverage/traceability reports; all device execution remains outside M0.

**Tech Stack:** Python standard library, `unittest`, JSON.

## M0 acceptance contract

- One source and approved requirement have stable IDs and versions.
- One build has artifact identity, package/version, environment source and runtime server/resource/DLL fields; unknown runtime fields remain explicit blockers.
- One approved case binds requirement, source, risk, testpoint, build, environment, device/account aliases, initial state, independent expectation, recovery permission and evidence requirements.
- One planned or blocked run binds case version, build, environment, device/account aliases and initial state, with attempt metadata.
- Evidence records have run/attempt backlinks, type, path policy, hash/collection state, sensitivity and retention fields.
- Reports show planned, executed, blocked and evidence-complete counts; blocked or planned runs never count as executed passes.
- No package installation, ADB, screenshot capture, model call, game account change or game source edit is part of M0.

## Verification commands

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p "test_m0*.py" -v
python -m compileall -q src
python -m ai_test_agent validate-m0 fixtures/m0-registry.example.json
python -m ai_test_agent report-m0 fixtures/m0-registry.example.json --output .local/m0-report.json
```

Expected coverage for the fixture: one planned case, zero executed cases, one blocked run, zero evidence-complete cases. This is the M0 closed loop; M1 begins only after this output is reviewed.
