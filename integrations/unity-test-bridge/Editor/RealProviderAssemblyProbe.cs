#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using UnityEngine;

namespace AiTestAgent.Bridge.Editor
{
    // An isolated Editor probe of freshly compiled bytes. This does not boot the game.
    public static class RealProviderAssemblyProbe
    {
        private const string IdentityMarker = "AI_TEST_LOADED_DLL_JSON:";
        [Serializable] private sealed class Availability { public bool available; }
        [Serializable] private sealed class LedgerAvailability { public bool available; public bool complete; }
        [Serializable] private sealed class ColdObservation
        {
            public Availability source;
            public Availability target;
            public Availability wallet;
            public LedgerAvailability ledger;
        }
        [Serializable]
        private sealed class Input
        {
            public string dll_path;
            public string expected_sha256;
            public string expected_mvid;
            public string expected_build_id;
            public string output_path;
            public string[] dependency_paths;
        }

        [Serializable]
        private sealed class Result
        {
            public int schema_version = 1;
            public string status = "PASS";
            public string scope = "editor_loaded_game_assembly_probe";
            public string business_status = "NOT_EVALUATED";
            public string assembly_name;
            public string dll_sha256;
            public string module_mvid;
            public string embedded_build_id;
            public string identity_line;
            public string cold_observation_json;
            public string observation_line;
        }

        public static void Run()
        {
            string inputPath = Environment.GetEnvironmentVariable("AI_TEST_PROVIDER_PROBE_INPUT");
            if (string.IsNullOrEmpty(inputPath)) throw new InvalidOperationException("Explicit probe input required");
            Input input = JsonUtility.FromJson<Input>(File.ReadAllText(inputPath));
            if (input == null || input.dependency_paths == null || File.Exists(input.output_path))
                throw new InvalidOperationException("Probe requires dependencies and a new output file");
            byte[] bytes = File.ReadAllBytes(input.dll_path);
            string digest;
            using (var sha = SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            if (digest != input.expected_sha256) throw new InvalidOperationException("Fresh DLL hash mismatch");
            var dependencies = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string path in input.dependency_paths)
            {
                try
                {
                    string name = AssemblyName.GetAssemblyName(path).FullName;
                    if (!dependencies.ContainsKey(name)) dependencies.Add(name, path);
                }
                catch (BadImageFormatException) { /* Native or analyzer data is not a runtime dependency. */ }
            }
            ResolveEventHandler resolver = (sender, args) => {
                foreach (Assembly existing in AppDomain.CurrentDomain.GetAssemblies())
                    if (existing.FullName == args.Name) return existing;
                string path;
                return dependencies.TryGetValue(args.Name, out path) ? Assembly.LoadFrom(path) : null;
            };
            string captured = null;
            Application.LogCallback onLog = (message, stack, kind) => {
                if (message.StartsWith(IdentityMarker, StringComparison.Ordinal)) captured = message;
            };
            AppDomain.CurrentDomain.AssemblyResolve += resolver;
            Application.logMessageReceived += onLog;
            try
            {
                Assembly assembly = Assembly.Load(bytes);
                string mvid = assembly.ManifestModule.ModuleVersionId.ToString("D").ToLowerInvariant();
                if (assembly.GetName().Name != "hlwd.game" || mvid != input.expected_mvid)
                    throw new InvalidOperationException("Fresh loaded module differs from compile metadata");
                Type stamp = assembly.GetType("AiTestAgent.Generated.ProviderBuildStamp", true);
                string embedded = (string)stamp.GetField("Value", BindingFlags.Public | BindingFlags.Static).GetRawConstantValue();
                if (embedded != input.expected_build_id) throw new InvalidOperationException("Embedded input stamp differs from manifest");
                Type bootstrap = assembly.GetType("AiTestAgent.IdleOutpost.IdleOutpostBridgeBootstrap", true);
                MethodInfo capture = bootstrap.GetMethod("CaptureLoadedAssembly", BindingFlags.Public | BindingFlags.Static,
                    null, new[] { typeof(byte[]), typeof(string) }, null);
                if (capture == null) throw new InvalidOperationException("Explicit identity entry missing");
                capture.Invoke(null, new object[] { bytes, mvid });
                if (captured == null) throw new InvalidOperationException("Loaded game assembly emitted no identity");
                Type identityType = assembly.GetType("AiTestAgent.Bridge.GameTestLoadedAssemblyIdentity", true);
                object identity = Activator.CreateInstance(identityType, new object[] { assembly.GetName().Name });
                identityType.GetMethod("Capture").Invoke(identity, new object[] { bytes, assembly });
                Type providerType = assembly.GetType("AiTestAgent.IdleOutpost.IdleOutpostReadOnlyProvider", true);
                object provider = Activator.CreateInstance(providerType, new object[] { identity, embedded, "1016", 0, 0 });
                object observed = providerType.GetMethod("ReadObservation").Invoke(provider, null);
                // Set probe transport metadata only; no controller or business object is changed.
                Type observedType = observed.GetType();
                observedType.GetField("session_id").SetValue(observed, "assembly-probe-session");
                observedType.GetField("seq").SetValue(observed, 1L);
                observedType.GetField("provider").SetValue(observed, "IdleOutpostReadOnlyProvider/v1");
                Type serializer = assembly.GetType("AiTestAgent.Bridge.GameTestObservationJson", true);
                string coldJson = (string)serializer.GetMethod("Serialize").Invoke(null, new[] { observed });
                ColdObservation cold = JsonUtility.FromJson<ColdObservation>(coldJson);
                if (cold.source == null || cold.target == null || cold.wallet == null || cold.ledger == null ||
                    !coldJson.Contains("\"events\":[]"))
                    throw new InvalidOperationException("Dynamic wire serialization omitted required sections or array");
                if (cold.source.available || cold.wallet.available || cold.target.available || cold.ledger.available || cold.ledger.complete)
                    throw new InvalidOperationException("Cold game with no controllers invented available data");
                var result = new Result { assembly_name = assembly.GetName().Name, dll_sha256 = digest,
                    module_mvid = mvid, embedded_build_id = embedded, identity_line = captured, cold_observation_json = coldJson,
                    observation_line = "AI_TEST_OBSERVATION_JSON:" + coldJson };
                using (var stream = new FileStream(input.output_path, FileMode.CreateNew, FileAccess.Write))
                using (var writer = new StreamWriter(stream)) writer.Write(JsonUtility.ToJson(result, true));
                Debug.Log("REAL_PROVIDER_ASSEMBLY_PROBE_PASS (Editor assembly identity only; no game bootstrap)");
            }
            finally
            {
                Application.logMessageReceived -= onLog;
                AppDomain.CurrentDomain.AssemblyResolve -= resolver;
            }
        }
    }
}
#endif
