#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System;
using System.Globalization;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace AiTestAgent.Bridge
{
    [Serializable]
    public sealed class LoadedAssemblyEvidence
    {
        public int schema = 1;
        public string runtime_kind;
        public string build_id;
        public string assembly_name;
        public string dll_sha256;
        public string module_mvid;
    }

    // The loader must pass the exact byte[] used by Assembly.Load and that returned Assembly.
    // This object records that explicit boundary; it does not discover asset files or git state.
    public sealed class GameTestLoadedAssemblyIdentity
    {
        public const string Marker = "AI_TEST_LOADED_DLL_JSON:";
        public bool Available { get; private set; }
        public string AssemblyName { get; private set; }
        public string DllSha256 { get; private set; }
        public string ModuleMvid { get; private set; }
        private Assembly capturedAssembly;
        private bool invalidated;

        public GameTestLoadedAssemblyIdentity(string assemblyName)
        {
            if (!Known(assemblyName)) throw new ArgumentException("Explicit assembly name required");
            AssemblyName = assemblyName;
        }

        public void Capture(byte[] loadedBytes, Assembly loadedAssembly)
        {
            if (invalidated) throw new InvalidOperationException("Conflicting capture invalidated this identity context");
            try
            {
                if (loadedBytes == null || loadedBytes.Length == 0 || loadedBytes.Length > 256 * 1024 * 1024)
                    throw new ArgumentException("Actual loaded DLL bytes are required within the 256 MiB budget");
                if (loadedAssembly == null || loadedAssembly.GetName().Name != AssemblyName)
                    throw new ArgumentException("Actual loaded assembly does not match its explicit name");
                Guid mvid = loadedAssembly.ManifestModule.ModuleVersionId;
                if (mvid == Guid.Empty) throw new InvalidOperationException("Loaded module identity is unavailable");
                string observedMvid = mvid.ToString("D").ToLowerInvariant();
                string observedHash;
                using (var sha = SHA256.Create())
                {
                    byte[] digest = sha.ComputeHash(loadedBytes);
                    var text = new StringBuilder(digest.Length * 2);
                    foreach (byte value in digest) text.Append(value.ToString("x2", CultureInfo.InvariantCulture));
                    observedHash = text.ToString();
                }
                if (Available && (capturedAssembly != loadedAssembly || DllSha256 != observedHash || ModuleMvid != observedMvid))
                    throw new InvalidOperationException("Loaded identity changed; create a new capture context");
                capturedAssembly = loadedAssembly;
                DllSha256 = observedHash;
                ModuleMvid = observedMvid;
                Available = true;
            }
            catch
            {
                if (Available)
                {
                    Available = false;
                    invalidated = true;
                    capturedAssembly = null;
                    DllSha256 = null;
                    ModuleMvid = null;
                }
                throw;
            }
        }

        public RuntimeSource ReadSource(string buildId, string resourceVersion, string server)
        {
            if (!Available || !Known(buildId) || !Known(resourceVersion) || !Known(server)) return new RuntimeSource();
            return new RuntimeSource {
                available = true, build_id = buildId, resource_version = resourceVersion,
                dll_version = "sha256:" + DllSha256, server = server
            };
        }

        public string SerializeEvidence(string buildId, string runtimeKind)
        {
            if (!Available) throw new InvalidOperationException("No loaded assembly identity has been captured");
            if (!Known(buildId)) throw new ArgumentException("Explicit runtime build stamp required");
            if (runtimeKind != "editor_probe" && runtimeKind != "android_player")
                throw new ArgumentException("Runtime kind must explicitly distinguish Editor from Android");
            return Marker + JsonUtility.ToJson(new LoadedAssemblyEvidence {
                runtime_kind = runtimeKind, build_id = buildId, assembly_name = AssemblyName,
                dll_sha256 = DllSha256, module_mvid = ModuleMvid
            });
        }

        private static bool Known(string value)
        {
            return !string.IsNullOrWhiteSpace(value) && value.Length <= 512;
        }
    }
}
#endif
