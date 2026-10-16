#if UNITY_EDITOR
using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using UnityEngine;

namespace AiTestAgent.Bridge.Editor
{
    public static class LoadedIdentityCompileSmoke
    {
        public static void Run()
        {
            Assembly assembly = typeof(LoadedIdentityCompileSmoke).Assembly;
            var identity = new GameTestLoadedAssemblyIdentity(assembly.GetName().Name);
            if (identity.Available || identity.ReadSource("synthetic-build", "1", "test").available)
                throw new InvalidOperationException("Uncaptured identity became available");
            ExpectFailure(() => identity.SerializeEvidence("synthetic-build", "editor_probe"));
            ExpectFailure(() => identity.Capture(new byte[0], assembly));
            if (identity.Available) throw new InvalidOperationException("Failed capture changed state");
            byte[] bytes = File.ReadAllBytes(assembly.Location);
            identity.Capture(bytes, assembly);
            string digest;
            using (var sha = SHA256.Create()) digest = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            if (!identity.Available || identity.DllSha256 != digest ||
                identity.ModuleMvid != assembly.ManifestModule.ModuleVersionId.ToString("D").ToLowerInvariant())
                throw new InvalidOperationException("Loaded assembly identity differs from actual bytes/module");
            identity.Capture(bytes, assembly);
            var source = identity.ReadSource("synthetic-build", "1016", "TestServer");
            if (!source.available || source.dll_version != "sha256:" + digest || source.build_id != "synthetic-build")
                throw new InvalidOperationException("Runtime source did not bind actual identity");
            if (identity.ReadSource("", "1016", "TestServer").available)
                throw new InvalidOperationException("Unknown stamp became available");
            ExpectFailure(() => identity.SerializeEvidence("synthetic-build", "unverified"));
            Debug.Log(identity.SerializeEvidence("synthetic-build", "editor_probe"));
            byte[] changed = (byte[])bytes.Clone(); changed[0] ^= 1;
            ExpectFailure(() => identity.Capture(changed, assembly));
            if (identity.Available || identity.ReadSource("synthetic-build", "1016", "TestServer").available)
                throw new InvalidOperationException("Conflicting capture retained trusted source");
            ExpectFailure(() => identity.SerializeEvidence("synthetic-build", "editor_probe"));
            ExpectFailure(() => identity.Capture(bytes, assembly));
            Debug.Log("LOADED_IDENTITY_SMOKE_PASS");
        }

        private static void ExpectFailure(Action action)
        {
            try { action(); }
            catch (ArgumentException) { return; }
            catch (InvalidOperationException) { return; }
            throw new InvalidOperationException("Expected fail-closed identity operation");
        }
    }
}
#endif
