#!/usr/bin/env python3
"""Render every block definition in a Bedrock add-on pack."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from json_utils import load_jsonc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-root", required=True, type=Path, help="Folder containing BP and RP")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--view", default="iso-ne")
    parser.add_argument("--resolution", default="80x80")
    parser.add_argument("--render-resolution", help="High-resolution Blender source, such as 1024x1024")
    parser.add_argument("--background", default="transparent")
    parser.add_argument("--lighting", choices=["studio", "flat", "dramatic"], default="studio")
    parser.add_argument("--blender")
    return parser.parse_args()


def block_identifier(path: Path) -> str:
    try:
        data = load_jsonc(path)
        identifier = data["minecraft:block"]["description"]["identifier"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Invalid Bedrock block definition: {path}") from exc
    return str(identifier)


def main() -> None:
    args = parse_args()
    pack_root = args.pack_root.expanduser().resolve()
    bp = pack_root / "BP"
    rp = pack_root / "RP"
    if not bp.is_dir() or not rp.is_dir():
        raise SystemExit(f"Expected BP and RP folders below: {pack_root}")
    block_paths = sorted((bp / "blocks").rglob("*.json"))
    if not block_paths:
        raise SystemExit(f"No block definitions found below: {bp / 'blocks'}")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    launcher = Path(__file__).with_name("render_model.py").resolve()
    texture_root = rp / "textures" / "blocks"
    failures: list[str] = []
    for index, model in enumerate(block_paths, 1):
        identifier = block_identifier(model)
        filename = identifier.split(":")[-1] + ".png"
        destination = output / filename
        command = [
            sys.executable, str(launcher),
            "--model", str(model),
            "--textures", str(texture_root),
            "--resource-pack", str(rp),
            "--output", str(destination),
            "--view", args.view,
            "--resolution", args.resolution,
            "--background", args.background,
            "--lighting", args.lighting,
            "--texture-filter", "closest",
        ]
        if args.blender:
            command.extend(["--blender", args.blender])
        if args.render_resolution:
            command.extend(["--render-resolution", args.render_resolution])
        print(f"[{index}/{len(block_paths)}] {identifier} -> {destination.name}", flush=True)
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            failures.append(identifier)
    if failures:
        raise SystemExit("Failed renders: " + ", ".join(failures))
    print(f"Rendered {len(block_paths)} blocks into {output}")


if __name__ == "__main__":
    main()
