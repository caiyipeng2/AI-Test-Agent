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
            CheckLoadedPeIdentity(assembly, bytes);
            byte[] changed = (byte[])bytes.Clone(); changed[0] ^= 1;
            ExpectFailure(() => identity.Capture(changed, assembly));
            if (identity.Available || identity.ReadSource("synthetic-build", "1016", "TestServer").available)
                throw new InvalidOperationException("Conflicting capture retained trusted source");
            ExpectFailure(() => identity.SerializeEvidence("synthetic-build", "editor_probe"));
            ExpectFailure(() => identity.Capture(bytes, assembly));
            Debug.Log("LOADED_IDENTITY_SMOKE_PASS");
        }

        private static void CheckLoadedPeIdentity(Assembly assembly, byte[] bytes)
        {
            var method = typeof(GameTestLoadedAssemblyIdentity).GetMethod("CaptureFromLoadedPe",
                new[] { typeof(byte[]), typeof(Assembly) });
            if (method == null) throw new InvalidOperationException("Explicit loaded-PE capture API is missing");
            var sourceProperty = typeof(GameTestLoadedAssemblyIdentity).GetProperty("MvidSource");
            if (sourceProperty == null) throw new InvalidOperationException("MVID provenance is missing");
            var pe = new GameTestLoadedAssemblyIdentity(assembly.GetName().Name);
            method.Invoke(pe, new object[] { bytes, assembly });
            if (!pe.Available || pe.ModuleMvid != assembly.ManifestModule.ModuleVersionId.ToString("D").ToLowerInvariant() ||
                (string)sourceProperty.GetValue(pe, null) != "loaded_pe_metadata" || new LoadedAssemblyEvidence().schema != 2)
                throw new InvalidOperationException("Loaded PE identity or schema2 provenance differs from actual bytes");
            ExpectFailure(() => pe.SerializeEvidence("synthetic-pe-build", "editor_probe"));
            string line = pe.SerializeEvidence("synthetic-pe-build", "android_player");
            if (!line.Contains("loaded_pe_metadata")) throw new InvalidOperationException("PE source was omitted from evidence");
            // A host whose RuntimeModule API is unavailable must still capture the exact PE bytes.
            var unsupported = new ModuleApiUnavailableAssembly(assembly.GetName());
            var unavailableApi = new GameTestLoadedAssemblyIdentity(assembly.GetName().Name);
            method.Invoke(unavailableApi, new object[] { bytes, unsupported });
            if (!unavailableApi.Available || unavailableApi.ModuleMvid != pe.ModuleMvid)
                throw new InvalidOperationException("PE capture tried to use the unavailable runtime module API");
            ExpectFailure(() => pe.Capture(bytes, assembly));
            if (pe.Available) throw new InvalidOperationException("Identity source switch retained stale trust");
            Debug.Log("LOADED_PE_IDENTITY_SMOKE_PASS");
        }

        private sealed class ModuleApiUnavailableAssembly : Assembly
        {
            private readonly AssemblyName name;
            public ModuleApiUnavailableAssembly(AssemblyName name) { this.name = name; }
            public override AssemblyName GetName(bool copiedName) { return name; }
            public override Module ManifestModule { get { throw new InvalidOperationException("Runtime module GUID unavailable"); } }
        }

        private static void ExpectFailure(Action action)
        {
            try { action(); }
            catch (ArgumentException) { return; }
            catch (InvalidOperationException) { return; }
            catch (FormatException) { return; }
            catch (BadImageFormatException) { return; }
            catch (InvalidDataException) { return; }
            throw new InvalidOperationException("Expected fail-closed identity operation");
        }
    }
}
#endif
