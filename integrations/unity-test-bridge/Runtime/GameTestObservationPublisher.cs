#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System;
using System.Globalization;
using System.Text;
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
            string message = Marker + GameTestObservationJson.Serialize(envelope);
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

    // Unity's native serializer can omit nested fields in assemblies loaded from bytes.
    // This fixed wire schema works in both imported and hot-loaded assemblies.
    public static class GameTestObservationJson
    {
        public static string Serialize(GameTestObservation value)
        {
            if (value == null || value.source == null || value.target == null || value.wallet == null ||
                value.ledger == null || value.ledger.events == null)
                throw new InvalidOperationException("Explicit observation sections are required");
            var text = new StringBuilder(1024);
            text.Append('{'); Field(text, "schema", value.schema); Field(text, "session_id", value.session_id);
            Field(text, "seq", value.seq); Field(text, "frame", value.frame); Field(text, "provider", value.provider);
            text.Append("\"source\":{");
            Field(text, "available", value.source.available); Field(text, "build_id", value.source.build_id);
            Field(text, "resource_version", value.source.resource_version); Field(text, "dll_version", value.source.dll_version);
            Field(text, "server", value.source.server); Close(text);
            text.Append(",\"target\":{");
            Field(text, "available", value.target.available); Field(text, "target_id", value.target.target_id);
            Field(text, "window_id", value.target.window_id); Field(text, "level", value.target.level);
            Field(text, "cost", value.target.cost); Field(text, "ready", value.target.ready); Close(text);
            text.Append(",\"wallet\":{");
            Field(text, "available", value.wallet.available); Field(text, "currency_id", value.wallet.currency_id);
            Field(text, "balance", value.wallet.balance); Close(text);
            text.Append(",\"ledger\":{");
            Field(text, "available", value.ledger.available); Field(text, "complete", value.ledger.complete);
            Field(text, "first_seq", value.ledger.first_seq); Field(text, "last_seq", value.ledger.last_seq);
            text.Append("\"events\":[");
            for (int index = 0; index < value.ledger.events.Length; index++)
            {
                LedgerEventSnapshot entry = value.ledger.events[index];
                if (entry == null) throw new InvalidOperationException("Ledger events cannot be null");
                if (index != 0) text.Append(',');
                text.Append('{'); Field(text, "seq", entry.seq); Field(text, "transaction_id", entry.transaction_id);
                Field(text, "operation_id", entry.operation_id); Field(text, "kind", entry.kind);
                Field(text, "target_id", entry.target_id); Field(text, "currency_id", entry.currency_id);
                Field(text, "delta", entry.delta); Field(text, "balance_after", entry.balance_after); Close(text);
            }
            return text.Append("]}}").ToString();
        }

        private static void Close(StringBuilder text) { text.Length--; text.Append('}'); }
        private static void Name(StringBuilder text, string name) { Quote(text, name); text.Append(':'); }
        private static void Field(StringBuilder text, string name, string value)
        { Name(text, name); Quote(text, value); text.Append(','); }
        private static void Field(StringBuilder text, string name, long value)
        { Name(text, name); text.Append(value.ToString(CultureInfo.InvariantCulture)).Append(','); }
        private static void Field(StringBuilder text, string name, bool value)
        { Name(text, name); text.Append(value ? "true," : "false,"); }
        private static void Quote(StringBuilder text, string value)
        {
            if (value == null) { text.Append("null"); return; }
            text.Append('"');
            foreach (char character in value)
            {
                if (character == '"' || character == '\\') text.Append('\\').Append(character);
                else if (character < 0x20 || char.IsSurrogate(character))
                    text.Append("\\u").Append(((int)character).ToString("x4", CultureInfo.InvariantCulture));
                else text.Append(character);
            }
            text.Append('"');
        }
    }
}
#endif
