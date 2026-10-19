#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace AiTestAgent.Bridge
{
    // This reads the Module MVID stored in the exact loaded PE bytes. It does not
    // claim that an IL2CPP RuntimeModule API supplied or independently verified it.
    public static class GameTestPeModuleIdentity
    {
        public const int MaximumBytes = 256 * 1024 * 1024;

        public static Guid ReadMvid(byte[] bytes)
        {
            if (bytes == null || bytes.Length == 0 || bytes.Length > MaximumBytes)
                throw new ArgumentException("Loaded PE bytes are required within the 256 MiB budget", nameof(bytes));
            return new Reader(bytes).Read();
        }

        private sealed class Reader
        {
            private readonly byte[] bytes;
            private Section[] sections;
            private uint headersSize;

            public Reader(byte[] bytes) { this.bytes = bytes; }

            public Guid Read()
            {
                Need(0, 64);
                Check(U16(0) == 0x5a4d, "Missing DOS signature");
                uint peValue = U32(0x3c);
                Check(peValue >= 64 && peValue <= (uint)bytes.Length, "Invalid PE header pointer");
                int pe = (int)peValue;
                Need(pe, 24);
                Check(U32(pe) == 0x00004550, "Missing PE signature");
                int sectionCount = U16(pe + 6);
                Check(sectionCount > 0 && sectionCount <= 96, "Invalid PE section count");
                int optionalSize = U16(pe + 20);
                int optional = pe + 24;
                Need(optional, optionalSize);
                Check(optionalSize >= 64, "Truncated optional header");
                int magic = U16(optional);
                Check(magic == 0x10b || magic == 0x20b, "Unsupported optional header format");
                int directoriesRelative = magic == 0x10b ? 96 : 112;
                Check(optionalSize >= directoriesRelative + 15 * 8, "Missing CLI data directory");
                uint directoryCount = U32(optional + directoriesRelative - 4);
                Check(directoryCount >= 15 && (ulong)directoriesRelative + (ulong)directoryCount * 8 <= (ulong)optionalSize,
                    "Data-directory count exceeds its optional header");
                headersSize = U32(optional + 60);
                int sectionHeaders = optional + optionalSize;
                Need(sectionHeaders, sectionCount * 40);
                Check(headersSize >= (ulong)sectionHeaders + (ulong)sectionCount * 40 && headersSize <= (uint)bytes.Length,
                    "Invalid size of PE headers");
                sections = new Section[sectionCount];
                for (int i = 0; i < sectionCount; i++)
                {
                    int at = sectionHeaders + i * 40;
                    var section = new Section { VirtualSize = U32(at + 8), VirtualAddress = U32(at + 12),
                        RawSize = U32(at + 16), RawOffset = U32(at + 20) };
                    Check((ulong)section.VirtualAddress + Math.Max((ulong)section.VirtualSize, section.RawSize) <= 0x100000000UL,
                        "Section virtual range overflows RVA space");
                    if (section.RawSize != 0)
                    {
                        Check(section.RawOffset >= headersSize, "Section raw bytes overlap the PE headers");
                        FileRange(section.RawOffset, section.RawSize);
                    }
                    sections[i] = section;
                }
                int cliDirectory = optional + directoriesRelative + 14 * 8;
                uint cliSize = U32(cliDirectory + 4);
                Check(cliSize >= 72, "Truncated CLI directory");
                int cli = Map(U32(cliDirectory), cliSize);
                uint cliHeaderSize = U32(cli);
                Check(cliHeaderSize >= 72 && cliHeaderSize <= cliSize, "Invalid CLI header size");
                uint metadataSize = U32(cli + 12);
                Check(metadataSize >= 20, "Missing CLI metadata");
                int metadata = Map(U32(cli + 8), metadataSize);
                return ReadMetadata(metadata, (int)metadataSize);
            }

            private Guid ReadMetadata(int metadata, int size)
            {
                Region(metadata, size, metadata, 16);
                Check(U32(metadata) == 0x424a5342, "Missing CLI metadata signature");
                uint versionLength = U32(metadata + 12);
                Check(versionLength > 0 && (versionLength & 3) == 0 && (ulong)versionLength + 20 <= (ulong)size,
                    "Invalid metadata version length");
                int version = metadata + 16;
                Check(bytes[version + (int)versionLength - 1] == 0, "Unterminated metadata version");
                int cursor = version + (int)versionLength;
                Region(metadata, size, cursor, 4);
                int streamCount = U16(cursor + 2);
                Check(streamCount > 0 && streamCount <= 64, "Invalid metadata stream count");
                cursor += 4;
                var streams = new List<MetadataStream>();
                var names = new HashSet<string>(StringComparer.Ordinal);
                MetadataStream tables = null, guids = null;
                for (int i = 0; i < streamCount; i++)
                {
                    Region(metadata, size, cursor, 9);
                    uint relative = U32(cursor);
                    uint length = U32(cursor + 4);
                    int nameStart = cursor + 8;
                    int nameLength = 0;
                    for (; nameLength < 32; nameLength++)
                    {
                        Region(metadata, size, nameStart + nameLength, 1);
                        byte value = bytes[nameStart + nameLength];
                        if (value == 0) break;
                        Check(value >= 0x21 && value <= 0x7e, "Metadata stream name is not printable ASCII");
                    }
                    Check(nameLength > 0 && nameLength < 32, "Unterminated or empty metadata stream name");
                    string name = Encoding.ASCII.GetString(bytes, nameStart, nameLength);
                    Check(names.Add(name), "Duplicate metadata stream name");
                    int paddedName = (nameLength + 1 + 3) & ~3;
                    Region(metadata, size, nameStart, paddedName);
                    for (int padding = nameLength + 1; padding < paddedName; padding++)
                        Check(bytes[nameStart + padding] == 0, "Nonzero metadata stream-name padding");
                    Check((ulong)relative + length <= (ulong)size, "Metadata stream exceeds its root");
                    var stream = new MetadataStream { Relative = relative, Size = length };
                    streams.Add(stream);
                    if (name == "#~" || name == "#-")
                    {
                        Check(tables == null, "Ambiguous metadata table streams");
                        tables = stream;
                    }
                    if (name == "#GUID") guids = stream;
                    cursor = nameStart + paddedName;
                }
                Check(tables != null && guids != null, "Missing metadata table or GUID stream");
                uint headerLength = (uint)(cursor - metadata);
                foreach (MetadataStream stream in streams)
                    Check(stream.Relative >= headerLength, "Metadata stream overlaps its stream headers");
                for (int i = 0; i < streams.Count; i++)
                    for (int j = i + 1; j < streams.Count; j++)
                        if (streams[i].Size != 0 && streams[j].Size != 0)
                            Check((ulong)streams[i].Relative + streams[i].Size <= streams[j].Relative ||
                                (ulong)streams[j].Relative + streams[j].Size <= streams[i].Relative,
                                "Overlapping metadata streams");
                Check(guids.Size >= 16 && (guids.Size & 15) == 0, "Invalid GUID heap size");
                return ReadModule(metadata + (int)tables.Relative, (int)tables.Size,
                    metadata + (int)guids.Relative, (int)guids.Size);
            }

            private Guid ReadModule(int tables, int tablesSize, int guids, int guidSize)
            {
                Region(tables, tablesSize, tables, 24);
                Check(U32(tables) == 0 && bytes[tables + 7] == 1, "Invalid metadata tables header");
                byte heapSizes = bytes[tables + 6];
                ulong valid = U64(tables + 8);
                Check((valid & 1) != 0, "No Module table is declared");
                int cursor = tables + 24;
                uint moduleRows = 0;
                for (int bit = 0; bit < 64; bit++)
                {
                    if ((valid & (1UL << bit)) == 0) continue;
                    Region(tables, tablesSize, cursor, 4);
                    uint rows = U32(cursor);
                    if (bit == 0) moduleRows = rows;
                    cursor += 4;
                }
                Check(moduleRows == 1, "Exactly one Module row is required");
                if ((heapSizes & 0x40) != 0)
                {
                    Region(tables, tablesSize, cursor, 4);
                    cursor += 4;
                }
                int stringWidth = (heapSizes & 1) == 0 ? 2 : 4;
                int guidWidth = (heapSizes & 2) == 0 ? 2 : 4;
                Region(tables, tablesSize, cursor, 2 + stringWidth + 3 * guidWidth);
                int mvidAt = cursor + 2 + stringWidth;
                uint index = guidWidth == 2 ? U16(mvidAt) : U32(mvidAt);
                Check(index > 0 && index <= (uint)guidSize / 16, "Module MVID index exceeds the GUID heap");
                int at = guids + ((int)index - 1) * 16;
                Region(guids, guidSize, at, 16);
                var value = new byte[16];
                Array.Copy(bytes, at, value, 0, 16);
                Guid guid = new Guid(value);
                Check(guid != Guid.Empty, "Module MVID is empty");
                return guid;
            }

            private int Map(uint rva, uint size)
            {
                Check(rva != 0 && size != 0 && (ulong)rva + size <= 0x100000000UL, "Invalid RVA range");
                int matches = 0;
                ulong offset = 0;
                if (rva < headersSize)
                {
                    Check((ulong)rva + size <= headersSize, "RVA crosses the PE headers");
                    offset = rva;
                    matches++;
                }
                foreach (Section section in sections)
                {
                    ulong extent = Math.Max((ulong)section.VirtualSize, section.RawSize);
                    ulong virtualEnd = (ulong)section.VirtualAddress + extent;
                    ulong requestedEnd = (ulong)rva + size;
                    if (extent == 0 || requestedEnd <= section.VirtualAddress || (ulong)rva >= virtualEnd) continue;
                    Check(rva >= section.VirtualAddress && requestedEnd <= virtualEnd,
                        "RVA range crosses or partially overlaps a section boundary");
                    ulong delta = (ulong)rva - section.VirtualAddress;
                    Check(delta + size <= section.RawSize, "RVA range is not backed by raw section bytes");
                    offset = (ulong)section.RawOffset + delta;
                    matches++;
                }
                Check(matches == 1, "RVA mapping is missing or ambiguous");
                Check(offset + size <= (ulong)bytes.Length, "RVA maps outside the loaded bytes");
                return (int)offset;
            }

            private void FileRange(uint offset, uint size)
            {
                Check((ulong)offset + size <= (ulong)bytes.Length, "Raw PE section exceeds the loaded bytes");
            }

            private void Region(int start, int size, int at, int count)
            {
                Check(size >= 0 && count >= 0 && at >= start && (long)at + count <= (long)start + size,
                    "Truncated metadata region");
                Need(at, count);
            }

            private void Need(int at, int count)
            {
                Check(at >= 0 && count >= 0 && at <= bytes.Length - count, "Truncated PE bytes");
            }

            private ushort U16(int at)
            {
                Need(at, 2);
                return (ushort)(bytes[at] | bytes[at + 1] << 8);
            }

            private uint U32(int at)
            {
                Need(at, 4);
                return (uint)(bytes[at] | bytes[at + 1] << 8 | bytes[at + 2] << 16 | bytes[at + 3] << 24);
            }

            private ulong U64(int at) { return U32(at) | (ulong)U32(at + 4) << 32; }
            private static void Check(bool condition, string message) { if (!condition) throw new InvalidDataException(message); }
        }

        private sealed class Section { public uint VirtualSize, VirtualAddress, RawSize, RawOffset; }
        private sealed class MetadataStream { public uint Relative, Size; }
    }
}
#endif
