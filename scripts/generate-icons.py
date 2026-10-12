"""Generate the simple geometric PWA app mark without a raster dependency."""
import struct
import zlib
from pathlib import Path


def chunk(kind, data):
    return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)


for size in (192, 512):
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            nx, ny = x / size, y / size
            shield = .22 < nx < .78 and .18 < ny < .64 or .64 <= ny < .83 and abs(nx - .5) < (.83 - ny) * 1.47
            stripe = shield and .45 < nx < .55 and .31 < ny < .66
            row.extend((235, 242, 255, 255) if stripe else (64, 120, 238, 255) if shield else (16, 21, 30, 255))
        rows.append(b"\0" + row)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
    (Path(__file__).resolve().parents[1] / "frontend" / "public" / f"icon-{size}.png").write_bytes(png)
