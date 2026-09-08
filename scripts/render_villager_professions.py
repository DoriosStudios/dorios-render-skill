#!/usr/bin/env python3
"""Render the thirteen working adult villager professions."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image


PROFESSIONS = (
    "armorer",
    "butcher",
    "cartographer",
    "cleric",
    "farmer",
    "fisherman",
    "fletcher",
    "leatherworker",
    "librarian",
    "shepherd",
    "stonemason",
    "toolsmith",
    "weaponsmith",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resolution", default="80x80")
    parser.add_argument("--render-resolution")
    parser.add_argument("--view", default="iso-ne")
    parser.add_argument("--lighting", default="vanilla")
    parser.add_argument("--background", default="transparent")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--renderer", type=Path)
    parser.add_argument("--blender")
    parser.add_argument("--include", help="Comma-separated profession names")
    return parser.parse_args()


def texture_path(root: Path, relative: str) -> Path:
    for extension in (".png", ".tga", ".jpg", ".jpeg"):
        candidate = root / (relative + extension)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(relative)


def alpha_composite(paths: list[Path], destination: Path) -> None:
    layers = [Image.open(path).convert("RGBA") for path in paths]
    size = layers[0].size
    if any(layer.size != size for layer in layers):
        raise ValueError("villager texture layers must have the same dimensions")
    composed = Image.new("RGBA", size, (0, 0, 0, 0))
    for layer in layers:
        composed = Image.alpha_composite(composed, layer)
    composed.save(destination)


def villager_model(source: Path, destination: Path) -> None:
    document = json.loads(source.read_text(encoding="utf-8"))
    geometries = document.get("minecraft:geometry", [])
    if not geometries:
        raise ValueError("geometry.villager_v2 was not found")
    bones = geometries[0].get("bones", [])
    brim = next((bone for bone in bones if bone.get("name") == "brim"), None)
    if brim is None:
        raise ValueError("villager brim bone was not found")
    # Mojang's adult villager geometry omits the bind rotation present on the
    # matching zombie-villager brim. Without it, job hats stand in the face.
    brim["bind_pose_rotation"] = [-90.0, 0.0, 0.0]
    destination.write_text(json.dumps(document), encoding="utf-8")


def main() -> None:
    args = arguments()
    if args.jobs < 1:
        raise SystemExit("--jobs must be at least 1")
    source = args.source_root.expanduser().resolve()
    resource_pack = source / "resource_pack"
    textures = resource_pack / "textures" / "entity" / "villager2"
    model_source = resource_pack / "models" / "entity" / "villager_v2.geo.json"
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    renderer = (
        args.renderer.expanduser().resolve()
        if args.renderer
        else Path(__file__).with_name("render_model.py")
    )
    requested = {
        value.strip().casefold()
        for value in (args.include or "").split(",")
        if value.strip()
    }
    professions = [name for name in PROFESSIONS if not requested or name in requested]
    unknown = sorted(requested.difference(PROFESSIONS))
    if unknown:
        raise SystemExit("Unknown professions: " + ", ".join(unknown))

    with tempfile.TemporaryDirectory(prefix="dorios-villager-professions-") as temporary:
        temporary_root = Path(temporary)
        model = temporary_root / "villager_v2.geo.json"
        villager_model(model_source, model)
        base_layers = [
            texture_path(textures, "villager"),
            texture_path(textures, "biomes/biome_plains"),
        ]
        level = texture_path(textures, "levels/level_stone")
        commands: list[tuple[str, list[str]]] = []
        for profession in professions:
            atlas = temporary_root / f"villager_{profession}.png"
            alpha_composite(
                [
                    *base_layers,
                    texture_path(textures, f"professions/{profession}"),
                    level,
                ],
                atlas,
            )
            command = [
                sys.executable,
                str(renderer),
                "--model", str(model),
                "--textures", str(atlas),
                "--geometry", "geometry.villager_v2",
                "--output", str(output / f"villager_{profession}.png"),
                "--view", args.view,
                "--resolution", args.resolution,
                "--background", args.background,
                "--lighting", args.lighting,
                "--ground", "off",
                "--texture-filter", "closest",
                "--bone-position", "arms=0,-1,-1",
                "--bone-rotation", "arms=-42.97,0,0",
            ]
            if args.render_resolution:
                command.extend(["--render-resolution", args.render_resolution])
            if args.blender:
                command.extend(["--blender", args.blender])
            commands.append((profession, command))

        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(subprocess.run, command, capture_output=True, text=True): profession
                for profession, command in commands
            }
            for future in as_completed(futures):
                profession = futures[future]
                completed = future.result()
                destination = output / f"villager_{profession}.png"
                try:
                    with Image.open(destination) as image:
                        valid = image.size == tuple(map(int, args.resolution.lower().split("x")))
                except OSError:
                    valid = False
                if completed.returncode != 0 or not valid:
                    failures.append(profession)
                    sys.stderr.write(completed.stderr)
                else:
                    print(f"rendered {profession}")
        if failures:
            raise SystemExit("Failed: " + ", ".join(sorted(failures)))


if __name__ == "__main__":
    main()
