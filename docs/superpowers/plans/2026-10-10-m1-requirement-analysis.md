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

## Current exit criteria

- Six M1 analyzer/review tests and one CLI test pass.
- Draft cases never invent price, level, target or window values.
- Dangling source anchors and approved-but-unreviewed cases fail closed.
- No device, package, game account, model API or game source is touched.
