#if UNITY_EDITOR
using System;
using System.Threading;
using UnityEngine;

namespace AiTestAgent.Bridge.Editor
{
    public static class ObservationCompileSmoke
    {
        // Explicit synthetic validation only; never discovers or connects to a game or device.
        public static void Run()
        {
            var host = new GameObject("SyntheticObservationCompileSmoke");
            try
            {
                var publisher = host.AddComponent<GameTestObservationPublisher>();
                ExpectInvalidOperation(() => publisher.PublishOnce(), "Missing provider must fail");
                var provider = new SyntheticProvider();
                publisher.Install(provider);
                GameTestObservation first = Read(publisher.PublishOnce());
                GameTestObservation second = Read(publisher.PublishOnce());
                Require(first.schema == 2 && first.seq == 1 && second.seq == 2, "Schema or sequence mismatch");
                Require(!string.IsNullOrEmpty(first.session_id) && first.session_id == second.session_id,
                    "One installation must retain its session");
                Require(first.frame >= 0 && second.frame >= first.frame && first.provider == provider.ProviderId,
                    "Frame or explicit provider identity mismatch");
                Require(first.source.available && first.source.build_id == "synthetic-build" &&
                    first.source.resource_version == "synthetic-resource" && first.source.dll_version == "synthetic-dll" &&
                    first.source.server == "synthetic-environment", "Source was not retained");
                Require(first.target.available && first.target.target_id == "synthetic-target" &&
                    first.target.window_id == "synthetic-window" && first.target.level == 2 &&
                    first.target.cost == "10.050000000000000000000000000001" && !first.target.ready,
                    "Target values or explicit false readiness changed");
                Require(first.wallet.available && first.wallet.currency_id == "synthetic-coins" &&
                    first.wallet.balance == "100.250000000000000000000000000001", "Exact wallet amount changed");
                Require(first.ledger.available && first.ledger.complete && first.ledger.first_seq == 1 &&
                    first.ledger.last_seq == 1 && first.ledger.events.Length == 1, "Complete ledger window changed");
                var entry = first.ledger.events[0];
                Require(entry.seq == 1 && entry.transaction_id == "synthetic-transaction" &&
                    entry.operation_id == "synthetic-operation" && entry.kind == "upgrade" &&
                    entry.target_id == "synthetic-target" && entry.currency_id == "synthetic-coins" &&
                    entry.delta == "-10.050000000000000000000000000001" &&
                    entry.balance_after == first.wallet.balance, "Ledger event changed");
                Require(provider.Value.session_id == null && provider.Value.seq == 0 && provider.Value.provider == null,
                    "Publisher must not mutate the provider observation");

                Exception threadFailure = null;
                int readsBefore = provider.ReadCount;
                var worker = new Thread(() =>
                {
                    try { publisher.PublishOnce(); }
                    catch (Exception error) { threadFailure = error; }
                });
                worker.Start();
                worker.Join();
                Require(threadFailure is InvalidOperationException && provider.ReadCount == readsBefore,
                    "Background sampling must fail before reading provider");
                threadFailure = null;
                worker = new Thread(() =>
                {
                    try { publisher.Install(provider); }
                    catch (Exception error) { threadFailure = error; }
                });
                worker.Start();
                worker.Join();
                Require(threadFailure is InvalidOperationException, "Background installation must fail");

                provider.Value.ledger.complete = false;
                GameTestObservation incomplete = Read(publisher.PublishOnce());
                Require(incomplete.seq == 3 && !incomplete.ledger.complete && !incomplete.target.ready,
                    "Incomplete ledger or readiness was promoted");

                publisher.Install(new UnavailableProvider());
                GameTestObservation unavailable = Read(publisher.PublishOnce());
                Require(unavailable.seq == 1 && unavailable.session_id != first.session_id,
                    "Explicit reinstall must start a new session");
                Require(!unavailable.source.available && unavailable.source.build_id == "" &&
                    unavailable.source.resource_version == "" && unavailable.source.dll_version == "" &&
                    unavailable.source.server == "", "Unknown source must remain unknown");
                Require(!unavailable.target.available && unavailable.target.target_id == "" &&
                    unavailable.target.window_id == "" && unavailable.target.level == -1 &&
                    unavailable.target.cost == "" && !unavailable.target.ready, "Unknown target must remain unknown");
                Require(!unavailable.wallet.available && unavailable.wallet.currency_id == "" &&
                    unavailable.wallet.balance == "", "Unknown wallet must remain unknown");
                Require(!unavailable.ledger.available && !unavailable.ledger.complete &&
                    unavailable.ledger.first_seq == 1 && unavailable.ledger.last_seq == 0 &&
                    unavailable.ledger.events.Length == 0, "Unknown ledger must remain unknown");

                CheckFailureRecovery(publisher);
                Debug.Log("OBSERVATION_COMPILE_SMOKE_PASS");
            }
            finally { UnityEngine.Object.DestroyImmediate(host); }
        }

        private static GameTestObservation Read(string message)
        {
            Require(message.StartsWith(GameTestObservationPublisher.Marker, StringComparison.Ordinal), "Wrong marker");
            return JsonUtility.FromJson<GameTestObservation>(message.Substring(GameTestObservationPublisher.Marker.Length));
        }

        private static void CheckFailureRecovery(GameTestObservationPublisher publisher)
        {
            var invalidValues = new GameTestObservation[] {
                null,
                new GameTestObservation { source = null },
                new GameTestObservation { target = null },
                new GameTestObservation { wallet = null },
                new GameTestObservation { ledger = null }
            };
            string[] cases = { "observation", "source", "target", "wallet", "ledger" };
            for (int index = 0; index < invalidValues.Length; index++)
            {
                var provider = new RecoveryProvider { Value = invalidValues[index] };
                string invalidBefore = provider.Value == null ? null : JsonUtility.ToJson(provider.Value);
                publisher.Install(provider);
                ExpectInvalidOperation(() => publisher.PublishOnce(), "Initial null " + cases[index] + " must fail");
                Require(ReferenceEquals(provider.Value, invalidValues[index]) &&
                    (provider.Value == null || JsonUtility.ToJson(provider.Value) == invalidBefore),
                    "Rejected provider data was mutated: " + cases[index]);

                var valid = new GameTestObservation();
                string validBefore = JsonUtility.ToJson(valid);
                provider.Value = valid;
                GameTestObservation recovered = Read(publisher.PublishOnce());
                Require(recovered.seq == 1 && !string.IsNullOrEmpty(recovered.session_id),
                    "Initial failed sample consumed a sequence: " + cases[index]);

                provider.Value = invalidValues[index];
                ExpectInvalidOperation(() => publisher.PublishOnce(), "Later null " + cases[index] + " must fail");
                Require(ReferenceEquals(provider.Value, invalidValues[index]) &&
                    (provider.Value == null || JsonUtility.ToJson(provider.Value) == invalidBefore),
                    "Later rejected provider data was mutated: " + cases[index]);
                provider.Value = valid;
                GameTestObservation recoveredAgain = Read(publisher.PublishOnce());
                Require(recoveredAgain.seq == 2 && recoveredAgain.session_id == recovered.session_id,
                    "Failed sample changed sequence or session: " + cases[index]);
                Require(ReferenceEquals(provider.Value, valid) && JsonUtility.ToJson(valid) == validBefore,
                    "Recovered provider data was mutated: " + cases[index]);
            }
            Debug.Log("OBSERVATION_RECOVERY_SMOKE_PASS cases=observation,source,target,wallet,ledger");
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }

        private static void ExpectInvalidOperation(Action action, string message)
        {
            try { action(); }
            catch (InvalidOperationException) { return; }
            throw new InvalidOperationException(message);
        }

        private sealed class SyntheticProvider : IGameTestObservationProvider
        {
            public string ProviderId { get { return "SyntheticObservationProvider"; } }
            public int ReadCount;
            public readonly GameTestObservation Value = new GameTestObservation {
                source = new RuntimeSource { available = true, build_id = "synthetic-build",
                    resource_version = "synthetic-resource", dll_version = "synthetic-dll", server = "synthetic-environment" },
                target = new ObservationTargetSnapshot { available = true, target_id = "synthetic-target",
                    window_id = "synthetic-window", level = 2, cost = "10.050000000000000000000000000001", ready = false },
                wallet = new WalletSnapshot { available = true, currency_id = "synthetic-coins",
                    balance = "100.250000000000000000000000000001" },
                ledger = new LedgerSnapshot { available = true, complete = true, first_seq = 1, last_seq = 1,
                    events = new[] { new LedgerEventSnapshot { seq = 1, transaction_id = "synthetic-transaction",
                        operation_id = "synthetic-operation", kind = "upgrade", target_id = "synthetic-target",
                        currency_id = "synthetic-coins", delta = "-10.050000000000000000000000000001",
                        balance_after = "100.250000000000000000000000000001" } } }
            };
            public GameTestObservation ReadObservation() { ReadCount++; return Value; }
        }

        private sealed class UnavailableProvider : IGameTestObservationProvider
        {
            public string ProviderId { get { return "SyntheticUnavailableProvider"; } }
            public GameTestObservation ReadObservation() { return new GameTestObservation(); }
        }

        private sealed class RecoveryProvider : IGameTestObservationProvider
        {
            public string ProviderId { get { return "SyntheticRecoveryProvider"; } }
            public GameTestObservation Value;
            public GameTestObservation ReadObservation() { return Value; }
        }
    }
}
#endif
