#if UNITY_EDITOR
using System;
using UnityEngine;

namespace AiTestAgent.Bridge.Editor
{
    public static class BridgeCompileSmoke
    {
        // Compilation and synthetic serialization only: this never connects to a game or device.
        public static void Run()
        {
            var host = new GameObject("SyntheticBridgeCompileSmoke");
            try
            {
                var publisher = host.AddComponent<GameTestStatePublisher>();
                publisher.Install(new SyntheticProvider());
                string message = publisher.PublishOnce();
                GameTestState state = JsonUtility.FromJson<GameTestState>(message.Substring(GameTestStatePublisher.Marker.Length));
                if (state.schema != 1 || state.seq != 1 || !state.target.available || state.target.level != 2 || state.target.coins != "100.25")
                    throw new InvalidOperationException("Synthetic snapshot serialization failed");
                Debug.Log("BRIDGE_COMPILE_SMOKE_PASS");
            }
            finally { UnityEngine.Object.DestroyImmediate(host); }
        }

        private sealed class SyntheticProvider : IGameTestStateProvider
        {
            public string ProviderId { get { return "SyntheticProvider"; } }
            public GameTestState ReadState()
            {
                return new GameTestState {
                    source = new RuntimeSource { available = true, build_id = "synthetic-build",
                        resource_version = "synthetic-resource", dll_version = "synthetic-dll", server = "synthetic-environment" },
                    target = new TargetSnapshot { available = true, target_id = "synthetic-target", level = 2,
                        coins = "100.25", cost = "10.05" }
                };
            }
        }
    }
}
#endif
