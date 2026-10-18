"""ppm2png -- convert a binary PPM (P6) to PNG, standard library only.

    python3 tools/ppm2png.py in.ppm out.png
"""
import struct
import sys
import zlib


def main(src, dst):
    data = open(src, "rb").read()
    toks, pos = [], 0
    while len(toks) < 4:                       # magic, width, height, maxval (comments start with #)
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        toks.append(data[pos:end])
        pos = end
    pos += 1                                   # the single whitespace byte after maxval
    if toks[0] != b"P6" or toks[3] != b"255":
        raise SystemExit("ppm2png: not an 8-bit P6 file")
    w, h = int(toks[1]), int(toks[2])
    pix = data[pos:pos + w * h * 3]
    if len(pix) != w * h * 3:
        raise SystemExit("ppm2png: truncated pixel data")
    raw = b"".join(b"\0" + pix[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(tag, body):
        c = struct.pack(">I", len(body)) + tag + body
        return c + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    open(dst, "wb").write(png)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
