#!/usr/bin/env python3
"""Resize 8-bit non-interlaced PNG files with exact nearest-neighbour sampling."""

from __future__ import annotations

import argparse
import binascii
import struct
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def paeth(a: int, b: int, c: int) -> int:
    estimate = a + b - c
    distance_a = abs(estimate - a)
    distance_b = abs(estimate - b)
    distance_c = abs(estimate - c)
    if distance_a <= distance_b and distance_a <= distance_c:
        return a
    return b if distance_b <= distance_c else c


def decode_png(path: Path) -> tuple[int, int, int, list[bytes], list[tuple[bytes, bytes]]]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"Not a PNG file: {path}")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    compressed = bytearray()
    metadata: list[tuple[bytes, bytes]] = []
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if compression != 0 or filtering != 0:
                raise ValueError("Unsupported PNG compression or filter method")
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        elif kind not in (b"PLTE", b"tRNS"):
            metadata.append((kind, payload))
    if None in (width, height, bit_depth, color_type, interlace):
        raise ValueError(f"PNG is missing IHDR: {path}")
    if bit_depth != 8 or color_type not in CHANNELS or interlace != 0:
        raise ValueError("Nearest resizer supports 8-bit, non-interlaced grayscale/RGB/RGBA PNG files")
    channels = CHANNELS[color_type]
    stride = width * channels
    raw = zlib.decompress(bytes(compressed))
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ValueError(f"Unexpected decompressed PNG size: {len(raw)} != {expected}")
    rows: list[bytes] = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        encoded = raw[cursor + 1 : cursor + 1 + stride]
        cursor += stride + 1
        decoded = bytearray(stride)
        for index, value in enumerate(encoded):
            left = decoded[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = paeth(left, above, upper_left)
            else:
                raise ValueError(f"Unsupported PNG row filter: {filter_type}")
            decoded[index] = (value + predictor) & 0xFF
        rows.append(bytes(decoded))
        previous = decoded
    return width, height, color_type, rows, metadata


def resize_png_nearest(source: Path, destination: Path, target_width: int, target_height: int) -> None:
    if target_width <= 0 or target_height <= 0:
        raise ValueError("Target dimensions must be positive")
    width, height, color_type, rows, metadata = decode_png(source)
    channels = CHANNELS[color_type]
    x_indices = [min(int((x + 0.5) * width / target_width), width - 1) for x in range(target_width)]
    y_indices = [min(int((y + 0.5) * height / target_height), height - 1) for y in range(target_height)]
    resized = bytearray()
    for source_y in y_indices:
        row = rows[source_y]
        resized.append(0)
        for source_x in x_indices:
            start = source_x * channels
            resized.extend(row[start : start + channels])
    ihdr = struct.pack(">IIBBBBB", target_width, target_height, 8, color_type, 0, 0, 0)
    output = bytearray(PNG_SIGNATURE)
    output.extend(chunk(b"IHDR", ihdr))
    for kind, payload in metadata:
        output.extend(chunk(kind, payload))
    output.extend(chunk(b"IDAT", zlib.compress(bytes(resized), level=9)))
    output.extend(chunk(b"IEND", b""))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--resolution", default="80x80")
    args = parser.parse_args()
    width, height = (int(value) for value in args.resolution.lower().split("x", 1))
    resize_png_nearest(args.source.resolve(), args.destination.resolve(), width, height)


if __name__ == "__main__":
    main()
