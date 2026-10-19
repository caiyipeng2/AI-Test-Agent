#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System;
using System.Globalization;
using AiTestAgent.Bridge;
using Hlwd.Aot;
using UnityEngine;
using UnityEngine.Scripting;

namespace AiTestAgent.IdleOutpost
{
    [Preserve]
    public sealed class IdleOutpostBridgeBootstrap : MonoBehaviour
    {
        public const int SampleEveryFrames = 30;
        private static readonly GameTestLoadedAssemblyIdentity Identity =
            new GameTestLoadedAssemblyIdentity(typeof(IdleOutpostBridgeBootstrap).Assembly.GetName().Name);
        private static bool started;
        private static IdleOutpostBridgeBootstrap activeHost;
        private static string resourceVersion = "";
        private GameTestObservationPublisher publisher;
        private int lastSampleFrame = -1;

        [Preserve]
        public static void CaptureLoadedAssembly(byte[] loadedBytes, string mvid)
        {
            // Compatibility signature only: Android IL2CPP may not implement RuntimeModule.ModuleVersionId.
            if (!Application.isEditor)
                throw new InvalidOperationException("Runtime module signature capture is supported only in the Unity Editor");
            var assembly = typeof(IdleOutpostBridgeBootstrap).Assembly;
            Guid claimedMvid;
            if (!Guid.TryParse(mvid, out claimedMvid) || claimedMvid != assembly.ManifestModule.ModuleVersionId)
                throw new ArgumentException("Loaded assembly MVID does not match this bootstrap", nameof(mvid));
            CaptureLoadedBytes(loadedBytes);
        }

        [Preserve]
        public static void CaptureLoadedBytes(byte[] loadedBytes)
        {
            if (!SupportedRuntime())
                throw new InvalidOperationException("Loaded identity requires the Unity Editor or an Android development player");
            var assembly = typeof(IdleOutpostBridgeBootstrap).Assembly;
            if (Application.isEditor) Identity.Capture(loadedBytes, assembly);
            else Identity.CaptureFromLoadedPe(loadedBytes, assembly);
            Debug.Log(Identity.SerializeEvidence(AiTestAgent.Generated.ProviderBuildStamp.Value,
                Application.isEditor ? "editor_probe" : "android_player"));
        }

        [Preserve]
        public static void Begin()
        {
            BeginCore(0, 0);
        }

        [Preserve]
        public static void BeginWithTarget(int terrainId, int deviceId)
        {
            if (terrainId <= 0) throw new ArgumentOutOfRangeException(nameof(terrainId), "Explicit terrain ID must be positive");
            if (deviceId <= 0) throw new ArgumentOutOfRangeException(nameof(deviceId), "Explicit device ID must be positive");
            BeginCore(terrainId, deviceId);
        }

        private static async void BeginCore(int terrainId, int deviceId)
        {
            // This entry is called only after cached controller initialization in the private test build.
            if (started || !SupportedRuntime() ||
                GameEntity.WebRequest == null || GameEntity.WebRequest.ServerType != ServerType.TestServer) return;
            started = true;
            bool initialized = false;
            GameObject host = null;
            try
            {
                var versions = await ResVersionHelper.LoadLocalResVersion();
                // Local loads StreamingAssets and ignores a higher persistent hotfix cache.
                // Develop has no packaged resource version; retain an unavailable source.
                var loadMode = BM.AssetComponentConfig.AssetLoadMode;
                int currentVersion = loadMode == BM.AssetLoadMode.Local ? versions.Item2 :
                    (loadMode == BM.AssetLoadMode.Build ? Math.Max(versions.Item1, versions.Item2) : 0);
                resourceVersion = currentVersion > 0 ? currentVersion.ToString(CultureInfo.InvariantCulture) : "";
                if (!SupportedRuntime() || GameEntity.WebRequest == null ||
                    GameEntity.WebRequest.ServerType != ServerType.TestServer) return;
                host = new GameObject("AiTestAgent.ReadOnlyBridge");
                UnityEngine.Object.DontDestroyOnLoad(host);
                var bootstrap = host.AddComponent<IdleOutpostBridgeBootstrap>();
                bootstrap.publisher = host.AddComponent<GameTestObservationPublisher>();
                bootstrap.publisher.Install(new IdleOutpostReadOnlyProvider(Identity,
                    AiTestAgent.Generated.ProviderBuildStamp.Value, resourceVersion, terrainId, deviceId));
                activeHost = bootstrap;
                initialized = true;
            }
            catch (Exception)
            {
                if (host != null) UnityEngine.Object.Destroy(host);
                Debug.LogWarning("AI_TEST_BRIDGE_UNAVAILABLE: bootstrap could not initialize");
            }
            finally
            {
                if (!initialized) started = false;
            }
        }

        private void Update()
        {
            if (publisher == null || GameEntity.WebRequest == null ||
                GameEntity.WebRequest.ServerType != ServerType.TestServer ||
                !SupportedRuntime()) return;
            int frame = Time.frameCount;
            if (frame == lastSampleFrame || frame % SampleEveryFrames != 0) return;
            lastSampleFrame = frame;
            try
            {
                // Repeat the already captured identity beside each current observation, so
                // slow startup never requires pairing current state with an old load log.
                Debug.Log(Identity.SerializeEvidence(AiTestAgent.Generated.ProviderBuildStamp.Value,
                    Application.isEditor ? "editor_probe" : "android_player"));
                publisher.PublishOnce();
            }
            catch (Exception) { Debug.LogWarning("AI_TEST_BRIDGE_UNAVAILABLE: observation sample rejected"); }
        }

        private void OnDestroy()
        {
            publisher = null;
            if (!ReferenceEquals(activeHost, this)) return;
            activeHost = null;
            started = false;
        }

        private static bool SupportedRuntime()
        {
            return Application.isEditor || (Application.platform == RuntimePlatform.Android && Debug.isDebugBuild);
        }
    }
}
#endif
