"""mkfat -- build a FAT16 "superfloppy" image (no MBR) pre-populated with files.

The Syborg non-volatile memory device backs drive C: with one raw image
(<qemu cwd>/nvmemory/syborg_system.img; syborg.dts: drive_size 0x4000000, 512-byte
sectors) and the media driver exposes the WHOLE image as a single FAT16
partition at offset 0 (syborg_mednvmemory.cpp PartitionInfo). This writes such
an image so C: boots with content -- e.g. fonts on a NON-ROM drive, which makes
fntstore take its RAM path like a real (NAND) phone does.

    py tools/mkfat.py out.img  'Resource/Fonts/ceurope.gdr=path/to/CEUROPE.GDR' [...]
        [--size 0x4000000]

8.3 names only (upper-cased); directories are created as needed.
"""
import struct, sys, time

SECTOR = 512


def _name83(name):
    base, _, ext = name.upper().partition(".")
    if not (0 < len(base) <= 8 and len(ext) <= 3):
        raise SystemExit(f"mkfat: '{name}' is not an 8.3 name")
    return base.ljust(8).encode("ascii") + ext.ljust(3).encode("ascii")


class Fat16:
    def __init__(self, size):
        self.total = size // SECTOR
        self.spc = 4                                  # 2 KB clusters
        self.reserved, self.nfats, self.root_ents = 1, 2, 512
        root_secs = self.root_ents * 32 // SECTOR
        data_guess = self.total - self.reserved - root_secs
        clusters = data_guess // self.spc
        self.fatsz = ((clusters + 2) * 2 + SECTOR - 1) // SECTOR
        self.first_data = self.reserved + self.nfats * self.fatsz + root_secs
        self.clusters = (self.total - self.first_data) // self.spc
        assert 4085 <= self.clusters < 65525, "not a FAT16 geometry"
        self.img = bytearray(size)
        self.fat = [0] * (self.clusters + 2)
        self.fat[0], self.fat[1] = 0xFFF8, 0xFFFF
        self.next_free = 2
        self.root = bytearray(self.root_ents * 32)
        self.dirs = {"": None}                        # path -> (cluster | None for root)
        self.dir_data = {}                            # cluster -> bytearray

    def _alloc(self, nbytes):
        n = max(1, (nbytes + self.spc * SECTOR - 1) // (self.spc * SECTOR))
        first = self.next_free
        for i in range(n):
            c = self.next_free
            self.next_free += 1
            self.fat[c] = c + 1 if i < n - 1 else 0xFFFF
        return first, n

    def _cluster_off(self, c):
        return (self.first_data + (c - 2) * self.spc) * SECTOR

    def _entry(self, name, attr, cluster, size):
        t = time.localtime()
        dt = ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
        tm = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
        return _name83(name) + struct.pack("<BBBHHHHHHHI", attr, 0, 0, tm, dt, dt, 0, tm, dt,
                                           cluster, size)

    def _add_entry(self, parent, ent):
        buf = self.root if parent is None else self.dir_data[parent]
        for off in range(0, len(buf), 32):
            if buf[off] == 0:
                buf[off:off + 32] = ent
                return
        raise SystemExit("mkfat: directory full")

    def mkdir(self, path):
        if path in self.dirs:
            return self.dirs[path]
        parent_path, _, name = path.rpartition("/")
        parent = self.mkdir(parent_path)
        c, _ = self._alloc(1)
        d = bytearray(self.spc * SECTOR)
        dot = bytearray(self._entry("X", 0x10, c, 0)); dot[0:11] = b".          "
        dd = bytearray(self._entry("X", 0x10, parent or 0, 0)); dd[0:11] = b"..         "
        d[0:32], d[32:64] = dot, dd
        self.dir_data[c] = d
        self._add_entry(parent, self._entry(name, 0x10, c, 0))
        self.dirs[path] = c
        return c

    def add_file(self, path, data):
        dpath, _, name = path.replace("\\", "/").strip("/").rpartition("/")
        parent = self.mkdir(dpath.upper()) if dpath else None
        c = 0
        if data:
            c, n = self._alloc(len(data))
            off = self._cluster_off(c)
            self.img[off:off + len(data)] = data
        self._add_entry(parent, self._entry(name, 0x20, c, len(data)))

    def finish(self):
        bpb = bytearray(SECTOR)
        bpb[0:3] = b"\xEB\x3C\x90"
        bpb[3:11] = b"MSDOS5.0"
        struct.pack_into("<HBHBHHBHHHII", bpb, 11, SECTOR, self.spc, self.reserved, self.nfats,
                         self.root_ents, self.total if self.total < 0x10000 else 0, 0xF8,
                         self.fatsz, 63, 255, 0, self.total if self.total >= 0x10000 else 0)
        struct.pack_into("<BBBI", bpb, 36, 0x80, 0, 0x29, int(time.time()) & 0xFFFFFFFF)
        bpb[43:54] = b"PHOREPOC   "
        bpb[54:62] = b"FAT16   "
        bpb[510:512] = b"\x55\xAA"
        self.img[0:SECTOR] = bpb
        fat = struct.pack(f"<{len(self.fat)}H", *self.fat)
        for i in range(self.nfats):
            off = (self.reserved + i * self.fatsz) * SECTOR
            self.img[off:off + len(fat)] = fat
        roff = (self.reserved + self.nfats * self.fatsz) * SECTOR
        self.img[roff:roff + len(self.root)] = self.root
        for c, d in self.dir_data.items():
            off = self._cluster_off(c)
            self.img[off:off + len(d)] = d
        return bytes(self.img)


def main():
    a = sys.argv[1:]
    size = 0x4000000
    if "--size" in a:
        i = a.index("--size"); size = int(a[i + 1], 0); del a[i:i + 2]
    out, specs = a[0], a[1:]
    fs = Fat16(size)
    for spec in specs:
        dst, _, src = spec.partition("=")
        fs.add_file(dst, open(src, "rb").read())
    open(out, "wb").write(fs.finish())
    print(f"wrote {out}: {size >> 20} MB FAT16, {fs.clusters} clusters, {len(specs)} files")


if __name__ == "__main__":
    main()
