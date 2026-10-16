# M3.2 Runtime Provider Implementation Plan

**Goal:** Build side-effect-free game adapters and truthful loaded-assembly identity, with isolated actual source compilation and device-free host verification.

**Architecture:** Add one generic runtime identity helper, two thin project adapter files, explicit test-only lifecycle hooks prepared in private source copies, and a host manifest verifier. Keep all copied game sources/dependencies and generated binaries ignored/local.

## 1. Provider and bootstrap

Files: `integrations/unity-test-bridge/Adapters/IdleOutpost/IdleOutpostReadOnlyProvider.cs`, `IdleOutpostBridgeBootstrap.cs`.

- Test missing controllers/keys and mismatched level/window/target produce unavailable without creating defaults. A missing/closed/mismatched window makes the entire target unavailable rather than emitting an available target with an empty window ID.
- Read only cached controllers/raw protobuf dictionaries, already-computed cost and loaded configuration scope. Ledger remains incomplete.
- Bootstrap uses explicit test guards, Android/Editor platform checks, actual server, generated build stamp and captured real DLL identity. Failed/deferred initialization and active-host destruction release only their own start state so explicit retries can recover. Patch only private source copies, with source hashes and exact unique anchors.

## 2. Loaded assembly identity

Files: `Runtime/GameTestLoadedAssemblyIdentity.cs`, `Editor/LoadedIdentityCompileSmoke.cs`.

- Write failing smoke for unloaded identity, exact SHA256/MVID, repeated/different capture and unknown values. A conflict after an accepted capture invalidates the old context permanently; no old source or recapture may revive it.
- Compile and run in isolated Unity Host. Actual game assembly probe separately loads freshly compiled hotfix bytes and calls preserved capture entry; do not treat it as Android gameplay.
- Serialize the fixed schema explicitly with `GameTestObservationJson.Serialize`: dynamic `Assembly.Load` must preserve every nested section/event. Verify the actual JSON with an independent parser, including Unicode/control escaping and 64-bit integer boundaries; never count `FromJson` default DTO initialization as evidence that missing wire fields existed.

## 3. Isolated actual source compiler

Files: `tools/compile_bridge_probe.py`, focused tool tests; private output under `.local/`.

- Freeze current hotfix source membership and dependencies, hash inputs and preserve dirty baseline.
- Normalize verified response files, regenerate stale source lists, redirect all compiler outputs, include bridge/adapter/stamp/hook copies.
- Actually compile Editor and Android test/release variants using Unity Roslyn. Record compiler args/defines/output identity. Compilation does not replace Unity Player/IL2CPP validation.

## 4. Host handshake

Files: `src/ai_test_agent/m3_handshake.py`, CLI command and tests.

- Validate manifest, actual DLL hash, exact loaded identity and mode separation; reject tampering and unsupported/partial device evidence. A device recording additionally pins the real capture log (`log_evidence={path, bytes, sha256}`), physical line ordering, current PID/time bracket and the M2 installed proof plus its referenced files.
- Preserve scope/business NOT_EVALUATED, device_free/live_provider=false and current evidence lineage; atomic JSON/Markdown outputs protect inputs. Protect both `path` and proof-parent-relative `relative_path` M2 evidence declarations even when their mismatch invalidates the proof. The command inspects supplied files and never authenticates their origin or establishes a currently live device connection.
- Execute compiled-assembly probe through real file pipeline, then negative/missing evidence branches.

## 5. Review and delivery

- Full Python regression, Unity smoke and actual game source compile evidence; independent reviews for readonly semantics and identity mode boundaries.
- Document patch/file placement and dedicated test build path, distinguish achieved compile/probe from unexecuted Android runtime steps.
- Commit/push M3.2 feature branch, leave accepted main and original game workspace intact until next checkpoint.

## Acceptance evidence and the next package loop

Use [the M3.2 guide](../../m3/runtime-provider.md) for the reusable structure, full input contract and acceptance checklist. Populate [the verification record](../../verification/2026-10-16-m32-runtime-provider.md) only from final command output; separate synthetic stubs, current game-source Roslyn compilation, actual Editor assembly loading and Android execution.

The AOT loader hook requires a dedicated full test build: freeze a compatible source/resource baseline, complete Unity import and AOT/IL2CPP, then package through the existing Android Studio SDK project. Verify the final package and actual installation through M2 before capturing device provider evidence. Do not replace the DLL in the old v65 package or treat Editor assembly PASS as game initialization, interactive readiness or business success. Full transaction collection, one safe UI upgrade and five baseline business reruns remain separate checkpoints.
