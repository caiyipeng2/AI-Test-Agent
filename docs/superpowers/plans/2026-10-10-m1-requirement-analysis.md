# M1 Requirement Analysis Implementation Plan

> **For agentic workers:** M1 only. Do not execute Android actions, install packages, call model APIs or modify the game project.

**Goal:** Turn authorized source sections into source-anchored risk, testpoint and case drafts with an explicit human review gate.

**Architecture:** A provider-neutral result schema accepts a deterministic offline baseline now and model adapters later. Every finding carries a source anchor; unknown product values remain unknown until a reviewer fills them.

**Tech Stack:** Python standard library, unittest, JSON.

## M1 closed loop

1. Normalize an authorized source document into stable sections.
2. Generate draft risks, testpoints and cases through a provider interface.
3. Validate all source anchors, IDs, review statuses and unknown-value rules.
4. Apply an explicit reviewer decision: approved, changes_requested or rejected.
5. Only an approved result with independent expectations may enter a later execution registry.
6. Record device-free manual results with evidence links and append-only history.
7. Normalize historical defects and produce coverage, lineage, review and execution gap reports.

## Current exit criteria

- M1 analyzer, strict import, manual result and gap-report tests pass.
- Draft cases never invent price, level, target or window values.
- Dangling source anchors and approved-but-unreviewed cases fail closed.
- Manual results cannot bypass case approval, assertion or evidence linkage.
- Historical defects cannot leak credentials/device identifiers or become M0 defects without confirmation and valid source anchors.
- No device, package, game account, model API or game source is touched.
