"""Deterministic bounded GPT/ESP FAT32 stream; no host disks or mount points.

512-byte logical sectors only. Free sectors are zeroed and the complete stream
is verified by the native writer. FAT32 rejects files >=4 GiB before any I/O.
"""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path, PurePosixPath
import re
import struct
from typing import Iterator
import uuid
import zlib

from macloader.removable.writer import WritePlan, UnsafeRemovableTarget

SECTOR = 512
CLUSTER = 4096
BLOCK = 1024 * 1024


@dataclass
class _Node:
    name: str
    children: dict[str, '_Node'] = field(default_factory=dict)
    source: Path | None = None
    size: int = 0
    digest: str = ''
    cluster: int = 0
    clusters: int = 0


def _lfn(name: str, alias: bytes) -> bytes:
    units = list(struct.unpack('<' + 'H' * (len(name.encode('utf-16le')) // 2), name.encode('utf-16le')))
    if not 1 <= len(units) <= 255:
        raise UnsafeRemovableTarget('FAT32 filename length is unsupported')
    checksum = 0
    for byte in alias:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 255
    units.append(0)
    while len(units) % 13:
        units.append(0xffff)
    records = []
    count = len(units) // 13
    for index in reversed(range(count)):
        group = units[index * 13:(index + 1) * 13]
        records.append(struct.pack('<B5HBBB6HH2H', (index + 1) | (0x40 if index == count - 1 else 0),
            *group[:5], 0x0f, 0, checksum, *group[5:11], 0, *group[11:]))
    return b''.join(records)



def _aliases(children: dict[str, _Node]) -> dict[str, bytes]:
    """Preserve canonical EFI/BOOT 8.3 names, with collision-free LFN aliases."""
    result: dict[str, bytes] = {}
    for name in children:
        fields = name.upper().split('.')
        stem, extension = fields[0], fields[1] if len(fields) == 2 else ''
        if len(fields) <= 2 and 1 <= len(stem) <= 8 and len(extension) <= 3 and all(re.fullmatch(r"[A-Z0-9!#$%&'()@^_`{}~-]*", part) for part in (stem, extension)):
            result[name] = stem.ljust(8).encode('ascii') + extension.ljust(3).encode('ascii')
    used = set(result.values())
    index = 1
    for name in children:
        if name in result:
            continue
        while True:
            alias = f'F{index:07d}'.encode('ascii') + b'   '
            index += 1
            if alias not in used:
                result[name] = alias
                used.add(alias)
                break
    return result

def _entry(alias: bytes, node: _Node, directory: bool) -> bytes:
    result = bytearray(32)
    result[:11] = alias
    result[11] = 0x10 if directory else 0x20
    struct.pack_into('<H', result, 20, node.cluster >> 16)
    struct.pack_into('<H', result, 26, node.cluster & 0xffff)
    struct.pack_into('<I', result, 28, 0 if directory else node.size)
    struct.pack_into('<H', result, 24, 0x5021)  # 2020-01-01, deterministic.
    return bytes(result)


class WindowsMediaImage:
    """Complete reproducible raw image of the existing immutable media plan."""
    def __init__(self, plan: WritePlan, source: Path):
        self.plan = plan
        capacity = plan.target.capacity_bytes
        if capacity % SECTOR or not 1024 ** 3 <= capacity <= 128 * 1024 ** 3:
            raise UnsafeRemovableTarget('Windows v1 requires a 1–128 GiB, 512-sector USB')
        if not plan.expected_files or plan.partitions != ('GPT', 'EFI', 'Recovery'):
            raise UnsafeRemovableTarget('Windows media layout is unsupported')
        self.sectors = capacity // SECTOR
        self.partition_sectors = ((self.sectors - 2048 - 33) // 2048) * 2048
        self.fat_sectors = 1
        while True:
            clusters = (self.partition_sectors - 32 - 2 * self.fat_sectors) // 8
            required = ((clusters + 2) * 4 + 511) // 512
            if required <= self.fat_sectors:
                break
            self.fat_sectors = required
        self.cluster_count = clusters
        if not 65525 <= clusters < 0x0ffffff5:
            raise UnsafeRemovableTarget('Windows media cannot use a valid FAT32 layout')
        self.data_offset = (2048 + 32 + 2 * self.fat_sectors) * SECTOR
        self.root = _Node('')
        self.nodes = [self.root]
        seen: set[str] = set()
        for record in plan.expected_files:
            rel = PurePosixPath(record.relative_path)
            if rel.is_absolute() or not rel.parts or any(part in ('.', '..') or any(c in part for c in '\\:*?"<>|') or part.endswith((' ', '.')) for part in rel.parts):
                raise UnsafeRemovableTarget('Unsafe Windows FAT32 media path')
            if record.size_bytes < 0 or record.size_bytes >= 2 ** 32:
                raise UnsafeRemovableTarget('Recovery/media file exceeds FAT32 size limit')
            key = rel.as_posix().casefold()
            if key in seen:
                raise UnsafeRemovableTarget('Case-insensitive media path collision')
            seen.add(key)
            node = self.root
            for part in rel.parts:
                if node.source is not None:
                    raise UnsafeRemovableTarget('Media file conflicts with a directory')
                match = next((item for name, item in node.children.items() if name.casefold() == part.casefold()), None)
                if match is not None and match.name != part:
                    raise UnsafeRemovableTarget('Case-insensitive directory collision')
                if match is None:
                    match = _Node(part)
                    node.children[part] = match
                    self.nodes.append(match)
                node = match
            if node.children:
                raise UnsafeRemovableTarget('Media directory conflicts with a file')
            node.source, node.size, node.digest = source.joinpath(*rel.parts), record.size_bytes, record.sha256
        next_cluster = 2
        for node in self.nodes:
            if node.source is None:
                length = 64 + sum(len(_lfn(child.name, b'F0000001   ')) + 32 for child in node.children.values()) + 32
            else:
                length = node.size
            node.clusters = (length + CLUSTER - 1) // CLUSTER
            node.cluster = next_cluster if node.clusters else 0
            next_cluster += node.clusters
        if next_cluster > self.cluster_count + 2:
            raise UnsafeRemovableTarget('USB lacks space for media filesystem overhead')
        fat = bytearray(self.fat_sectors * SECTOR)
        struct.pack_into('<II', fat, 0, 0x0ffffff8, 0x0fffffff)
        for node in self.nodes:
            for offset in range(node.clusters):
                cluster = node.cluster + offset
                struct.pack_into('<I', fat, cluster * 4, cluster + 1 if offset + 1 < node.clusters else 0x0fffffff)
        self.next_free_cluster = next_cluster
        self.fat = bytes(fat)
        self.extents: list[tuple[int, bytes | _Node]] = self._metadata()
        self._directories(self.root, self.root)
        for node in self.nodes:
            if node.source is not None and node.clusters:
                self.extents.append((self._offset(node), node))
        self.extents.sort(key=lambda item: item[0])

    def _offset(self, node: _Node) -> int:
        return self.data_offset + (node.cluster - 2) * CLUSTER

    def _directories(self, node: _Node, parent: _Node) -> None:
        from dataclasses import replace
        parent_ref = replace(parent, cluster=0) if parent is self.root else parent
        data = b'MACLOADER  ' + b'\x08' + bytes(20) if node is self.root else _entry(b'.          ', node, True) + _entry(b'..         ', parent_ref, True)
        aliases = _aliases(node.children)
        for child in node.children.values():
            alias = aliases[child.name]
            data += _lfn(child.name, alias) + _entry(alias, child, child.source is None)
            if child.source is None:
                self._directories(child, node)
        self.extents.append((self._offset(node), data.ljust(node.clusters * CLUSTER, b'\0')))

    def _metadata(self) -> list[tuple[int, bytes | _Node]]:
        identity = bytes.fromhex(self.plan.plan_digest)
        entries = bytearray(128 * 128)
        entries[:16] = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b').bytes_le
        entries[16:32] = uuid.UUID(bytes=identity[:16]).bytes_le
        struct.pack_into('<QQQ', entries, 32, 2048, 2048 + self.partition_sectors - 1, 0)
        entries[56:74] = 'MACLOADER'.encode('utf-16le')
        crc = zlib.crc32(entries)
        def header(current: int, backup: int, table: int) -> bytes:
            result = bytearray(SECTOR)
            struct.pack_into('<8sIIIIQQQQ16sQIII', result, 0, b'EFI PART', 0x10000, 92, 0, 0,
                current, backup, 34, self.sectors - 34, uuid.UUID(bytes=identity[16:]).bytes_le, table, 128, 128, crc)
            struct.pack_into('<I', result, 16, zlib.crc32(result[:92]))
            return bytes(result)
        mbr = bytearray(SECTOR)
        mbr[446:462] = struct.pack('<B3sB3sII', 0, b'\x00\x02\x00', 0xee, b'\xff' * 3, 1, min(self.sectors - 1, 0xffffffff))
        mbr[510:] = b'\x55\xaa'
        boot = bytearray(SECTOR)
        boot[:11] = b'\xeb\x58\x90MACLOADR'
        struct.pack_into('<HBHBHHBHHHII', boot, 11, 512, 8, 32, 2, 0, 0, 0xf8, 0, 63, 255, 2048, self.partition_sectors)
        struct.pack_into('<IHHIHH', boot, 36, self.fat_sectors, 0, 0, 2, 1, 6)
        boot[64], boot[66] = 0x80, 0x29
        boot[67:71] = identity[:4]
        boot[71:82], boot[82:90], boot[510:] = b'MACLOADER  ', b'FAT32   ', b'\x55\xaa'
        info = bytearray(SECTOR)
        struct.pack_into('<I', info, 0, 0x41615252)
        struct.pack_into('<III', info, 484, 0x61417272, self.cluster_count - (self.next_free_cluster - 2), self.next_free_cluster if self.next_free_cluster < self.cluster_count + 2 else 0xffffffff)
        struct.pack_into('<I', info, 508, 0xaa550000)
        base = 2048 * SECTOR
        return [(0, bytes(mbr)), (SECTOR, header(1, self.sectors - 1, 2)), (2 * SECTOR, bytes(entries)),
            ((self.sectors - 33) * SECTOR, bytes(entries)), ((self.sectors - 1) * SECTOR, header(self.sectors - 1, 1, self.sectors - 33)),
            (base, bytes(boot)), (base + SECTOR, bytes(info)), (base + 6 * SECTOR, bytes(boot)), (base + 7 * SECTOR, bytes(info)),
            (base + 32 * SECTOR, self.fat), (base + (32 + self.fat_sectors) * SECTOR, self.fat)]

    def chunks(self) -> Iterator[tuple[int, bytes]]:
        """Aligned, bounded chunks cover the entire selected disk, including gaps."""
        offset = 0
        for start, content in self.extents:
            if start < offset:
                raise UnsafeRemovableTarget('Overlapping Windows media layout')
            while offset < start:
                block = bytes(min(BLOCK, start - offset))
                yield offset, block
                offset += len(block)
            if isinstance(content, bytes):
                for pos in range(0, len(content), BLOCK):
                    block = content[pos:pos + BLOCK]
                    yield offset, block
                    offset += len(block)
            else:
                assert content.source is not None
                if content.source.is_symlink() or content.source.stat().st_size != content.size:
                    raise UnsafeRemovableTarget('Windows media source changed')
                digest = hashlib.sha256()
                with content.source.open('rb') as handle:
                    remaining = content.size
                    while remaining:
                        block = handle.read(min(BLOCK, remaining))
                        if not block:
                            raise UnsafeRemovableTarget('Windows media source ended unexpectedly')
                        remaining -= len(block)
                        digest.update(block)
                        padding = (-len(block)) % SECTOR
                        yield offset, block + bytes(padding)
                        offset += len(block) + padding
                if digest.hexdigest() != content.digest:
                    raise UnsafeRemovableTarget('Windows media source digest changed')
                end = start + content.clusters * CLUSTER
                if offset < end:
                    yield offset, bytes(end - offset)
                    offset = end
        while offset < self.plan.target.capacity_bytes:
            block = bytes(min(BLOCK, self.plan.target.capacity_bytes - offset))
            yield offset, block
            offset += len(block)
