#!/usr/bin/env python3
"""Launch Blender to create a deterministic model render."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from nearest_resize import center_png_alpha, resize_png_nearest


def blender_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("BLENDER_EXE")
    if env_path:
        candidates.append(Path(env_path))
    found = shutil.which("blender")
    if found:
        candidates.append(Path(found))
    if sys.platform == "win32":
        root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
        if root.is_dir():
            candidates.extend(sorted(root.glob("Blender */blender.exe"), reverse=True))
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    else:
        candidates.extend([Path("/usr/bin/blender"), Path("/snap/bin/blender")])
    return candidates


def find_blender(explicit: str | None) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise SystemExit(f"Blender executable not found: {candidate}")
    for candidate in blender_candidates():
        if candidate.is_file():
            return candidate.resolve()
    raise SystemExit(
        "Blender 3.6+ was not found. Install Blender, add it to PATH, set BLENDER_EXE, "
        "or pass --blender <path>. No AI-generated fallback was created."
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--model")
    source.add_argument("--manifest", help="JSON/JSONC multi-block scene manifest")
    result.add_argument("--textures", nargs="*", default=[])
    result.add_argument("--output", required=True)
    result.add_argument("--source-output", help="Optional path that keeps the high-resolution Blender render")
    result.add_argument("--no-center-content", action="store_true", help="Do not center transparent visible bounds")
    result.add_argument("--view", default="iso-ne", choices=[
        "iso-ne", "iso-nw", "iso-se", "iso-sw", "front", "back", "left", "right", "top", "custom"
    ])
    result.add_argument("--azimuth", type=float, default=45.0)
    result.add_argument("--elevation", type=float, default=30.0)
    result.add_argument("--model-rotation", default="0,0,0")
    result.add_argument("--hide-bone", action="append", default=[])
    result.add_argument("--bone-rotation", action="append", default=[])
    result.add_argument("--geometry")
    result.add_argument("--resource-pack", help="Bedrock RP folder; inferred from a sibling BP folder when omitted")
    result.add_argument("--ortho-scale", type=float)
    result.add_argument("--resolution", default="80x80")
    result.add_argument("--render-resolution", help="High-resolution Blender source; defaults to at least 1024 pixels")
    result.add_argument("--margin", type=float, default=0.025)
    result.add_argument("--samples", type=int, default=64)
    result.add_argument("--background", default="transparent")
    result.add_argument(
        "--lighting",
        choices=["balanced", "left_light", "right_light", "studio", "flat", "dramatic"],
        default="right_light",
    )
    result.add_argument("--ground", choices=["auto", "on", "off"], default="auto")
    result.add_argument("--no-shadows", action="store_true")
    result.add_argument("--texture-filter", choices=["closest", "linear"], default="closest")
    result.add_argument("--blender")
    result.add_argument("--dry-run", action="store_true")
    return result


def dimensions(value: str, label: str) -> tuple[int, int]:
    try:
        width, height = (int(item) for item in value.lower().split("x", 1))
    except (ValueError, TypeError) as exc:
        raise SystemExit(f"Invalid {label}: {value}; expected WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        raise SystemExit(f"Invalid {label}: dimensions must be positive")
    return width, height


def source_dimensions(final: tuple[int, int], explicit: str | None) -> tuple[int, int]:
    if explicit:
        source = dimensions(explicit, "render resolution")
        if source[0] * final[1] != source[1] * final[0]:
            raise SystemExit("--render-resolution must have the same aspect ratio as --resolution")
        if source[0] < final[0] or source[1] < final[1]:
            raise SystemExit("--render-resolution cannot be smaller than the final --resolution")
        return source
    longest = max(final)
    if longest >= 1024:
        return final
    factor = 1024 / longest
    return max(round(final[0] * factor), 1), max(round(final[1] * factor), 1)


def main() -> None:
    args = parser().parse_args()
    source_path = Path(args.model or args.manifest).expanduser().resolve()
    if not source_path.is_file():
        label = "Model" if args.model else "Manifest"
        raise SystemExit(f"{label} not found: {source_path}")
    texture_paths = [Path(item).expanduser().resolve() for item in args.textures]
    missing = [path for path in texture_paths if not path.exists()]
    if missing:
        raise SystemExit("Texture path not found: " + ", ".join(map(str, missing)))
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    source_output = Path(args.source_output).expanduser().resolve() if args.source_output else None
    if source_output:
        source_output.parent.mkdir(parents=True, exist_ok=True)
    final_size = dimensions(args.resolution, "resolution")
    render_size = source_dimensions(final_size, args.render_resolution)
    blender = Path(args.blender) if args.dry_run and args.blender else (
        Path("blender") if args.dry_run else find_blender(args.blender)
    )
    worker = Path(__file__).with_name("blender_render.py").resolve()
    needs_resize = render_size != final_size
    with tempfile.TemporaryDirectory(prefix="dorios-render-") as temporary:
        if needs_resize:
            render_output = source_output or (Path(temporary) / "source.png")
        else:
            render_output = output
        forwarded = [
            "--manifest" if args.manifest else "--model", str(source_path),
            "--output", str(render_output), "--view", args.view,
            "--azimuth", str(args.azimuth), "--elevation", str(args.elevation),
            "--model-rotation", args.model_rotation,
            "--resolution", f"{render_size[0]}x{render_size[1]}",
            "--margin", str(args.margin), "--samples", str(args.samples),
            "--background", args.background, "--lighting", args.lighting,
            "--ground", args.ground, "--texture-filter", args.texture_filter,
        ]
        if texture_paths:
            forwarded.extend(["--textures", *map(str, texture_paths)])
        if args.geometry:
            forwarded.extend(["--geometry", args.geometry])
        if args.resource_pack:
            resource_pack = Path(args.resource_pack).expanduser().resolve()
            if not resource_pack.is_dir():
                raise SystemExit(f"Resource pack not found: {resource_pack}")
            forwarded.extend(["--resource-pack", str(resource_pack)])
        if args.ortho_scale is not None:
            forwarded.extend(["--ortho-scale", str(args.ortho_scale)])
        for name in args.hide_bone:
            forwarded.extend(["--hide-bone", name])
        for rotation in args.bone_rotation:
            forwarded.extend(["--bone-rotation", rotation])
        if args.no_shadows:
            forwarded.append("--no-shadows")
        command = [str(blender), "--background", "--factory-startup", "--python", str(worker), "--", *forwarded]
        if args.dry_run:
            print(subprocess.list2cmdline(command))
            if needs_resize:
                print(f"Then resize with nearest neighbour: {render_size[0]}x{render_size[1]} -> {final_size[0]}x{final_size[1]}")
            if args.background == "transparent" and not args.no_center_content:
                print("Then center the non-zero alpha bounds in the unchanged canvas")
            return
        print(f"Rendering {source_path.name} at {render_size[0]}x{render_size[1]} with Blender...")
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            raise SystemExit(completed.returncode)
        if not render_output.is_file() or render_output.stat().st_size == 0:
            raise SystemExit(f"Blender finished without creating a valid source render: {render_output}")
        if args.background == "transparent" and not args.no_center_content:
            shift_x, shift_y = center_png_alpha(render_output, render_output)
            print(f"Centered visible alpha bounds by {shift_x},{shift_y} source pixels")
        if needs_resize:
            resize_png_nearest(render_output, output, final_size[0], final_size[1])
        elif source_output and source_output != output:
            shutil.copyfile(output, source_output)
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit(f"Post-processing finished without creating a valid output: {output}")
    print(f"Created: {output}")


if __name__ == "__main__":
    main()
