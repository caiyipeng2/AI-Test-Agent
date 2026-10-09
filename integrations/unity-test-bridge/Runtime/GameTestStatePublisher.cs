#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System;
using System.Threading;
using UnityEngine;

namespace AiTestAgent.Bridge
{
    // The game integration supplies reads only; the bridge never discovers business controllers.
    public interface IGameTestStateProvider
    {
        string ProviderId { get; }
        GameTestState ReadState();
    }

    [Serializable]
    public sealed class RuntimeSource
    {
        public bool available;
        public string build_id = "";
        public string resource_version = "";
        public string dll_version = "";
        public string server = "";
    }

    [Serializable]
    public sealed class UiElementSnapshot
    {
        public string path;
        public float x;
        public float y;
        public float width;
        public float height;
    }

    [Serializable]
    public sealed class WindowSnapshot
    {
        public bool available;
        public UiElementSnapshot[] items = new UiElementSnapshot[0];
    }

    [Serializable]
    public sealed class TargetSnapshot
    {
        public bool available;
        public string target_id = "";
        // -1 denotes unavailable; it must never be interpreted as a measured level.
        public int level = -1;
        public string coins = "";
        public string cost = "";
    }

    [Serializable]
    public sealed class GameTestState
    {
        public int schema = 1;
        public string session_id;
        public long seq;
        public int frame;
        public string provider;
        public RuntimeSource source = new RuntimeSource();
        public WindowSnapshot windows = new WindowSnapshot();
        public TargetSnapshot target = new TargetSnapshot();
    }

    [ExecuteAlways]
    public sealed class GameTestStatePublisher : MonoBehaviour
    {
        public const string Marker = "AI_TEST_STATE_JSON:";
        private IGameTestStateProvider provider;
        private int sampleEveryFrames;
        private bool includeUi;
        private int mainThreadId;
        private string sessionId;
        private long sequence;
        private int lastFrame = -1;

        private void OnEnable()
        {
            mainThreadId = Thread.CurrentThread.ManagedThreadId;
        }

        public void Install(IGameTestStateProvider stateProvider, int everyFrames = 0, bool captureUi = false)
        {
            RequireMainThread();
            if (stateProvider == null) throw new ArgumentNullException(nameof(stateProvider));
            if (everyFrames < 0) throw new ArgumentOutOfRangeException(nameof(everyFrames));
            if (string.IsNullOrWhiteSpace(stateProvider.ProviderId)) throw new ArgumentException("Explicit provider ID required");
            provider = stateProvider;
            sampleEveryFrames = everyFrames;
            includeUi = captureUi;
            sessionId = Guid.NewGuid().ToString("N");
            sequence = 0;
            lastFrame = -1;
        }

        private void Update()
        {
            if (provider == null || sampleEveryFrames == 0) return;
            int frame = Time.frameCount;
            if (frame != lastFrame && frame % sampleEveryFrames == 0) PublishOnce();
        }

        public string PublishOnce()
        {
            RequireMainThread();
            if (provider == null) throw new InvalidOperationException("Provider must be explicitly installed");
            GameTestState state = provider.ReadState();
            if (state == null) throw new InvalidOperationException("Provider returned no state");
            state.schema = 1;
            state.session_id = sessionId;
            state.seq = ++sequence;
            state.frame = Time.frameCount;
            state.provider = provider.ProviderId;
            state.source = state.source ?? new RuntimeSource();
            state.windows = state.windows ?? new WindowSnapshot();
            state.target = state.target ?? new TargetSnapshot();
            if (!state.target.available)
            {
                state.target.level = -1;
                state.target.coins = "";
                state.target.cost = "";
            }
            if (includeUi) state.windows = UgUiSnapshot.ReadActive();
            lastFrame = state.frame;
            string message = Marker + JsonUtility.ToJson(state);
            Debug.Log(message);
            return message;
        }

        private void RequireMainThread()
        {
            if (mainThreadId == 0 || Thread.CurrentThread.ManagedThreadId != mainThreadId)
                throw new InvalidOperationException("Snapshots must be installed and sampled on the Unity main thread");
        }
    }
}
#endif
