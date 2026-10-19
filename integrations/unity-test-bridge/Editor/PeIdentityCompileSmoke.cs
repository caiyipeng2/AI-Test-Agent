#if UNITY_EDITOR || AI_TEST_PE_SMOKE
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;

namespace AiTestAgent.Bridge.Editor
{
    public static class PeIdentityCompileSmoke
    {
        private static int passed;

#if UNITY_EDITOR
        public static void Run()
        {
            string configured = Environment.GetEnvironmentVariable("AI_TEST_PE_FIXTURES");
            string[] paths = string.IsNullOrWhiteSpace(configured)
                ? new[] { typeof(PeIdentityCompileSmoke).Assembly.Location }
                : configured.Split(new[] { Path.PathSeparator }, StringSplitOptions.RemoveEmptyEntries);
            Verify(paths);
            UnityEngine.Debug.Log("PE_IDENTITY_SMOKE_PASS cases=" + passed);
        }
#endif

        public static int Main(string[] paths)
        {
            try
            {
                Require(paths.Length > 0, "Pass one or more actual compiled DLL fixtures");
                Verify(paths);
                Console.WriteLine("PE_IDENTITY_SMOKE_PASS cases=" + passed);
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine(error); return 1; }
        }

        private static void Verify(string[] paths)
        {
            passed = 0;
            CheckFixtureHost(paths);
            foreach (string path in paths)
            {
                byte[] bytes = File.ReadAllBytes(path);
                Guid actual = Assembly.ReflectionOnlyLoad(bytes).ManifestModule.ModuleVersionId;
                Require(actual != Guid.Empty, "The fixture runtime returned no MVID");
                Require(GameTestPeModuleIdentity.ReadMvid(bytes) == actual, "PE MVID differs from Mono/Editor runtime MVID");
                passed++;
                Console.WriteLine("PASS actual-runtime-mvid " + Path.GetFileName(path));
                CorruptionChecks(bytes, actual);
            }
            Reject(null, "null");
            Reject(new byte[0], "empty");
            Reject(new byte[256 * 1024 * 1024 + 1], "oversized");
        }

        private static void CheckFixtureHost(string[] paths)
        {
            var identities = new HashSet<string>(StringComparer.Ordinal);
            foreach (string path in paths)
            {
                string identity = AssemblyName.GetAssemblyName(path).FullName;
                Require(identities.Add(identity), "Duplicate fixture assembly identity: " + identity +
                    ". Validate each fixture in a separate process; ReflectionOnlyLoad caches assembly identities.");
            }
            foreach (string path in paths)
            {
                string identity = AssemblyName.GetAssemblyName(path).FullName;
                foreach (Assembly cached in AppDomain.CurrentDomain.ReflectionOnlyGetAssemblies())
                {
                    if (cached.GetName().FullName != identity) continue;
                    Guid fileMvid = GameTestPeModuleIdentity.ReadMvid(File.ReadAllBytes(path));
                    Require(cached.ManifestModule.ModuleVersionId == fileMvid,
                        "Reflection-only fixture host cache collision: " + identity +
                        ". Validate this fixture in a separate process; its MVID differs from the already loaded image.");
                }
            }
        }

        private static void CorruptionChecks(byte[] original, Guid expected)
        {
            Layout layout = FindLayout(original);
            foreach (int size in new[] { 1, 63, layout.Pe + 3, layout.Optional + 2,
                layout.SectionHeaders + 39, layout.Cli + 71, layout.Metadata + 15,
                layout.Metadata + layout.MetadataSize - 1 })
            {
                var shortened = new byte[Math.Min(size, original.Length)];
                Array.Copy(original, shortened, shortened.Length);
                Reject(shortened, "truncated-" + size);
            }
            Reject(Change(original, b => b[0] = 0), "dos-signature");
            Reject(Change(original, b => b[layout.Pe] = 0), "pe-signature");
            Reject(Change(original, b => Put16(b, layout.Optional, 0x999)), "optional-magic");
            Reject(Change(original, b => Put32(b, layout.CliDirectory, 0)), "missing-cli");
            Reject(Change(original, b => Put32(b, layout.Cli, 71)), "cli-header-too-small");
            Reject(Change(original, b => Put32(b, layout.Cli + 8, uint.MaxValue)), "metadata-rva-overflow");
            Reject(Change(original, b => Put32(b, layout.Cli + 12, uint.MaxValue)), "metadata-size-overflow");
            Reject(Change(original, b => b[layout.Metadata] = 0), "metadata-signature");
            Reject(Change(original, b => Put32(b, layout.Metadata + 12, uint.MaxValue)), "version-string-overflow");
            Reject(Change(original, b => Put16(b, layout.StreamCount, 0)), "missing-streams");
            Reject(Change(original, b => Put32(b, layout.Guid.Header, 0)), "guid-points-into-header");
            Reject(Change(original, b => Put32(b, layout.Guid.Header, (uint)(layout.Tables.Offset - layout.Metadata))), "overlapping-streams");
            Reject(Change(original, b => Put32(b, layout.Guid.Header + 4, (uint)(layout.Guid.Size - 1))), "guid-heap-not-16-byte-aligned");
            Reject(Change(original, b => b[layout.Tables.Offset + 8] &= 0xfe), "missing-module-valid-bit");
            Reject(Change(original, b => Put32(b, layout.Tables.Offset + 24, 0)), "no-module-row");
            Reject(Change(original, b => Put32(b, layout.Tables.Offset + 24, 2)), "ambiguous-module-rows");
            Reject(Change(original, b => Put32(b, layout.Tables.Header + 4, 27)), "truncated-row-counts");
            Reject(Change(original, b => PutIndex(b, layout.MvidIndex, layout.GuidWidth, 0)), "zero-guid-index");
            Reject(Change(original, b => PutIndex(b, layout.MvidIndex, layout.GuidWidth,
                layout.GuidWidth == 2 ? ushort.MaxValue : uint.MaxValue)), "guid-index-outside-heap");
            Reject(Change(original, b => Array.Clear(b, layout.MvidBytes, 16)), "empty-mvid");
            Reject(Change(original, b => {
                int first = layout.SectionHeaders;
                int second = first + 40;
                Require(layout.Sections >= 2, "Fixture needs two PE sections to test ambiguous RVA mapping");
                Put32(b, second + 12, U32(b, first + 12));
                Put32(b, second + 8, U32(b, first + 8));
                Put32(b, second + 16, U32(b, first + 16));
            }), "ambiguous-cli-rva");
            Reject(Change(original, b => {
                int second = layout.SectionHeaders + 40;
                Put32(b, second + 12, U32(b, layout.Cli + 8) + 16);
                Put32(b, second + 8, 16);
                Put32(b, second + 16, 16);
            }), "partly-ambiguous-metadata-rva");
            Stream blob;
            if (layout.Streams.TryGetValue("#Blob", out blob))
                Reject(Change(original, b => {
                    byte[] name = Encoding.ASCII.GetBytes("#GUID\0");
                    Array.Copy(name, 0, b, blob.Header + 8, name.Length);
                }), "duplicate-guid-stream");
            byte[] uncompressed = Change(original, b => {
                b[layout.Tables.Header + 8] = (byte)'#';
                b[layout.Tables.Header + 9] = (byte)'-';
            });
            Require(GameTestPeModuleIdentity.ReadMvid(uncompressed) == expected, "Uncompressed table stream MVID differs");
            passed++;
        }

        private static void Reject(byte[] bytes, string name)
        {
            try { GameTestPeModuleIdentity.ReadMvid(bytes); }
            catch (ArgumentException) { passed++; Console.WriteLine("PASS reject-" + name); return; }
            catch (InvalidDataException) { passed++; Console.WriteLine("PASS reject-" + name); return; }
            throw new InvalidOperationException("Malformed PE was accepted: " + name);
        }

        private static byte[] Change(byte[] original, Action<byte[]> mutate)
        {
            var copy = (byte[])original.Clone(); mutate(copy); return copy;
        }

        // This helper only locates fields in an already accepted compiler-produced fixture.
        // The reader under test owns all adversarial parsing and validation.
        private static Layout FindLayout(byte[] bytes)
        {
            var layout = new Layout();
            layout.Pe = (int)U32(bytes, 0x3c);
            layout.Sections = U16(bytes, layout.Pe + 6);
            layout.Optional = layout.Pe + 24;
            layout.SectionHeaders = layout.Optional + U16(bytes, layout.Pe + 20);
            int directory = layout.Optional + (U16(bytes, layout.Optional) == 0x10b ? 96 : 112);
            layout.CliDirectory = directory + 14 * 8;
            layout.Cli = FixtureRva(bytes, layout, U32(bytes, layout.CliDirectory));
            layout.Metadata = FixtureRva(bytes, layout, U32(bytes, layout.Cli + 8));
            layout.MetadataSize = (int)U32(bytes, layout.Cli + 12);
            layout.StreamCount = layout.Metadata + 16 + (int)U32(bytes, layout.Metadata + 12) + 2;
            int cursor = layout.StreamCount + 2;
            int count = U16(bytes, layout.StreamCount);
            for (int i = 0; i < count; i++)
            {
                int end = cursor + 8;
                while (bytes[end] != 0) end++;
                string name = Encoding.ASCII.GetString(bytes, cursor + 8, end - cursor - 8);
                layout.Streams.Add(name, new Stream { Header = cursor,
                    Offset = layout.Metadata + (int)U32(bytes, cursor), Size = (int)U32(bytes, cursor + 4) });
                cursor += 8 + ((end - cursor - 8 + 1 + 3) & ~3);
            }
            layout.Guid = layout.Streams["#GUID"];
            if (!layout.Streams.TryGetValue("#~", out layout.Tables)) layout.Tables = layout.Streams["#-"];
            byte heaps = bytes[layout.Tables.Offset + 6];
            int stringWidth = (heaps & 1) == 0 ? 2 : 4;
            layout.GuidWidth = (heaps & 2) == 0 ? 2 : 4;
            ulong valid = BitConverter.ToUInt64(bytes, layout.Tables.Offset + 8);
            int rowCounts = 0;
            for (int bit = 0; bit < 64; bit++) if ((valid & (1UL << bit)) != 0) rowCounts++;
            int rows = layout.Tables.Offset + 24 + rowCounts * 4 + ((heaps & 0x40) == 0 ? 0 : 4);
            layout.MvidIndex = rows + 2 + stringWidth;
            uint index = layout.GuidWidth == 2 ? U16(bytes, layout.MvidIndex) : U32(bytes, layout.MvidIndex);
            layout.MvidBytes = layout.Guid.Offset + ((int)index - 1) * 16;
            return layout;
        }

        private static int FixtureRva(byte[] bytes, Layout layout, uint rva)
        {
            for (int i = 0; i < layout.Sections; i++)
            {
                int section = layout.SectionHeaders + i * 40;
                uint start = U32(bytes, section + 12);
                uint size = U32(bytes, section + 16);
                if (rva >= start && (ulong)rva < (ulong)start + size)
                    return (int)(U32(bytes, section + 20) + rva - start);
            }
            throw new InvalidOperationException("Compiler fixture RVA was not mapped");
        }

        private static ushort U16(byte[] b, int p) { return BitConverter.ToUInt16(b, p); }
        private static uint U32(byte[] b, int p) { return BitConverter.ToUInt32(b, p); }
        private static void Put16(byte[] b, int p, ushort value) { Array.Copy(BitConverter.GetBytes(value), 0, b, p, 2); }
        private static void Put32(byte[] b, int p, uint value) { Array.Copy(BitConverter.GetBytes(value), 0, b, p, 4); }
        private static void PutIndex(byte[] b, int p, int width, uint value)
        {
            if (width == 2) Put16(b, p, (ushort)value); else Put32(b, p, value);
        }
        private static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }

        private sealed class Stream { public int Header, Offset, Size; }
        private sealed class Layout
        {
            public int Pe, Optional, SectionHeaders, Sections, CliDirectory, Cli, Metadata, MetadataSize,
                StreamCount, GuidWidth, MvidIndex, MvidBytes;
            public Stream Guid, Tables;
            public readonly Dictionary<string, Stream> Streams = new Dictionary<string, Stream>();
        }
    }
}
#endif
