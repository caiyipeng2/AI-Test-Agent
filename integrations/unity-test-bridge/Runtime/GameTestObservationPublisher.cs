#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System;
using System.Threading;
using UnityEngine;

namespace AiTestAgent.Bridge
{
    // Integration is explicit and read only; no controller discovery or business actions occur here.
    public interface IGameTestObservationProvider
    {
        string ProviderId { get; }
        GameTestObservation ReadObservation();
    }

    [Serializable]
    public sealed class ObservationTargetSnapshot
    {
        public bool available;
        public string target_id = "";
        public string window_id = "";
        public long level = -1;
        public string cost = "";
        public bool ready;
    }

    [Serializable]
    public sealed class WalletSnapshot
    {
        public bool available;
        public string currency_id = "";
        public string balance = "";
    }

    [Serializable]
    public sealed class LedgerEventSnapshot
    {
        public long seq;
        public string transaction_id = "";
        public string operation_id = "";
        public string kind = "";
        public string target_id = "";
        public string currency_id = "";
        public string delta = "";
        public string balance_after = "";
    }

    [Serializable]
    public sealed class LedgerSnapshot
    {
        public bool available;
        public bool complete;
        public long first_seq = 1;
        public long last_seq;
        public LedgerEventSnapshot[] events = new LedgerEventSnapshot[0];
    }

    [Serializable]
    public sealed class GameTestObservation
    {
        public int schema = 2;
        public string session_id;
        public long seq;
        public int frame;
        public string provider;
        public RuntimeSource source = new RuntimeSource();
        public ObservationTargetSnapshot target = new ObservationTargetSnapshot();
        public WalletSnapshot wallet = new WalletSnapshot();
        public LedgerSnapshot ledger = new LedgerSnapshot();
    }

    [ExecuteAlways]
    public sealed class GameTestObservationPublisher : MonoBehaviour
    {
        public const string Marker = "AI_TEST_OBSERVATION_JSON:";
        private IGameTestObservationProvider provider;
        private string providerId;
        private string sessionId;
        private long sequence;
        private int lastFrame = -1;
        private int mainThreadId;

        private void OnEnable()
        {
            mainThreadId = Thread.CurrentThread.ManagedThreadId;
        }

        public void Install(IGameTestObservationProvider observationProvider)
        {
            RequireMainThread();
            if (observationProvider == null) throw new ArgumentNullException(nameof(observationProvider));
            string identity = observationProvider.ProviderId;
            if (string.IsNullOrWhiteSpace(identity)) throw new ArgumentException("Explicit provider ID required");
            provider = observationProvider;
            providerId = identity;
            sessionId = Guid.NewGuid().ToString("N");
            sequence = 0;
            lastFrame = -1;
        }

        public string PublishOnce()
        {
            RequireMainThread();
            if (provider == null) throw new InvalidOperationException("Provider must be explicitly installed");
            GameTestObservation observation = provider.ReadObservation();
            if (observation == null) throw new InvalidOperationException("Provider returned no observation");
            if (observation.source == null || observation.target == null || observation.wallet == null ||
                observation.ledger == null || observation.ledger.events == null)
                throw new InvalidOperationException("Provider must explicitly supply observation sections");
            int frame = Time.frameCount;
            if (frame < 0 || frame < lastFrame)
                throw new InvalidOperationException("Unity frame regressed; explicitly reinstall for a new session");
            long nextSequence = checked(sequence + 1);
            // The envelope owns transport metadata. Provider data is serialized without mutation,
            // amount conversion, or promotion of available, complete, or ready flags.
            var envelope = new GameTestObservation {
                session_id = sessionId, seq = nextSequence, frame = frame, provider = providerId,
                source = observation.source, target = observation.target,
                wallet = observation.wallet, ledger = observation.ledger
            };
            string message = Marker + JsonUtility.ToJson(envelope);
            sequence = nextSequence;
            lastFrame = frame;
            Debug.Log(message);
            return message;
        }

        private void RequireMainThread()
        {
            if (mainThreadId == 0 || Thread.CurrentThread.ManagedThreadId != mainThreadId)
                throw new InvalidOperationException("Observations must be installed and sampled on the Unity main thread");
        }
    }
}
#endif
