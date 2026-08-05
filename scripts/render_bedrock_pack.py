#!/usr/bin/env python3
"""Render every block definition in a Bedrock add-on pack."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    parser.add_argument(
        "--lighting",
        choices=["balanced", "left_light", "right_light", "studio", "flat", "dramatic"],
        default="right_light",
    )
    parser.add_argument("--blender")
    parser.add_argument("--overrides", type=Path, help="JSON/JSONC block and path render overrides")
    parser.add_argument(
        "--exclude-path",
        action="append",
        default=[],
        help="Skip block paths containing this case-insensitive path fragment; repeat as needed",
    )
    parser.add_argument(
        "--include-path",
        action="append",
        default=[],
        help="Only render block paths containing this case-insensitive path fragment; repeat as needed",
    )
    parser.add_argument(
        "--include-identifier-suffix",
        action="append",
        default=[],
        help="Only render identifiers ending in this case-insensitive suffix; repeat as needed",
    )
    parser.add_argument(
        "--exclude-identifier-suffix",
        action="append",
        default=[],
        help="Skip identifiers ending in this case-insensitive suffix; repeat as needed",
    )
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent Blender processes (default: 1)")
    parser.add_argument("--skip-existing", action="store_true", help="Keep PNG files already present")
    parser.add_argument(
        "--material-permutation",
        choices=["auto", "base", "last"],
        default="auto",
        help="Render base materials or the last material-bearing block permutation",
    )
    parser.add_argument("--output-suffix", default="", help="Suffix inserted before .png, such as _off or _on")
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
    blocks_root = bp / "blocks"
    excluded_fragments = [value.replace("\\", "/").strip("/").casefold() for value in args.exclude_path]
    included_fragments = [value.replace("\\", "/").strip("/").casefold() for value in args.include_path]
    included_suffixes = [value.casefold() for value in args.include_identifier_suffix]
    excluded_suffixes = [value.casefold() for value in args.exclude_identifier_suffix]
    block_paths = [
        path
        for path in sorted(blocks_root.rglob("*.json"))
        if not included_fragments or any(
            fragment in path.relative_to(blocks_root).as_posix().casefold()
            for fragment in included_fragments
        )
        if not any(
            fragment in path.relative_to(blocks_root).as_posix().casefold()
            for fragment in excluded_fragments
        )
    ]
    if not block_paths:
        raise SystemExit(f"No block definitions found below: {blocks_root}")
    if args.jobs < 1:
        raise SystemExit("--jobs must be at least 1")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    overrides_path = args.overrides.expanduser().resolve() if args.overrides else pack_root / "Assets" / "render_overrides.json"
    overrides_data = load_jsonc(overrides_path) if overrides_path.is_file() else {}
    block_overrides = overrides_data.get("blocks", {}) if isinstance(overrides_data, dict) else {}
    path_rules = overrides_data.get("path_rules", []) if isinstance(overrides_data, dict) else []
    launcher = Path(__file__).with_name("render_model.py").resolve()
    # Block geometry may reference auxiliary material instances whose images
    # live outside textures/blocks (for example tank fluid/gas contents in
    # textures/entity).  Feed both catalogs to the model renderer so those
    # per-face material instances resolve instead of falling back to magenta.
    texture_roots = [rp / "textures" / "blocks", rp / "textures" / "entity"]
    # Some add-ons reuse geometry and material keys from a sibling base pack
    # (Ascendant Technology, for example, reuses UtilityCraft turbine blades
    # and power-beacon atlases). Keep this pack first so local assets win, then
    # expose sibling RP textures as dependency fallbacks.
    for sibling_rp in sorted(pack_root.parent.glob("*/RP")):
        if sibling_rp.resolve() == rp.resolve():
            continue
        texture_roots.extend([
            sibling_rp / "textures" / "blocks",
            sibling_rp / "textures" / "entity",
        ])
    texture_roots = [path for path in texture_roots if path.is_dir()]
    render_jobs: list[tuple[str, Path, list[str]]] = []
    destinations: dict[str, Path] = {}
    skipped = 0
    for model in block_paths:
        identifier = block_identifier(model)
        identifier_key = identifier.casefold()
        if included_suffixes and not any(identifier_key.endswith(suffix) for suffix in included_suffixes):
            continue
        if any(identifier_key.endswith(suffix) for suffix in excluded_suffixes):
            continue
        relative_model = model.relative_to(blocks_root).as_posix().casefold()
        settings: dict[str, object] = {}
        for rule in path_rules if isinstance(path_rules, list) else []:
            if not isinstance(rule, dict):
                continue
            fragment = str(rule.get("contains", "")).replace("\\", "/").strip("/").casefold()
            if fragment and fragment in relative_model:
                settings.update({key: value for key, value in rule.items() if key != "contains"})
        identifier_override = block_overrides.get(identifier, {}) if isinstance(block_overrides, dict) else {}
        if isinstance(identifier_override, dict):
            settings.update(identifier_override)
        render_model = model
        if "model" in settings:
            render_model = Path(str(settings["model"])).expanduser()
            if not render_model.is_absolute():
                render_model = pack_root / render_model
            render_model = render_model.resolve()
            if not render_model.is_file():
                raise SystemExit(f"Override model not found for {identifier}: {render_model}")
        job_texture_roots = texture_roots
        if "textures" in settings:
            texture_values = settings["textures"]
            if isinstance(texture_values, str):
                texture_values = [texture_values]
            if not isinstance(texture_values, list):
                raise SystemExit(f"Override textures for {identifier} must be a string or array")
            job_texture_roots = []
            for value in texture_values:
                texture_path = Path(str(value)).expanduser()
                if not texture_path.is_absolute():
                    texture_path = pack_root / texture_path
                texture_path = texture_path.resolve()
                if not texture_path.exists():
                    raise SystemExit(f"Override texture not found for {identifier}: {texture_path}")
                job_texture_roots.append(texture_path)
        filename = identifier.split(":")[-1] + args.output_suffix + ".png"
        destination = output / filename
        collision_key = filename.casefold()
        previous = destinations.get(collision_key)
        if previous is not None:
            raise SystemExit(f"Duplicate output filename {filename}: {previous} and {model}")
        destinations[collision_key] = model
        if args.skip_existing and destination.is_file():
            skipped += 1
            continue
        command = [
            sys.executable, str(launcher),
            "--model", str(render_model),
            "--textures", *(str(path) for path in job_texture_roots),
            "--resource-pack", str(rp),
            "--output", str(destination),
            "--view", str(settings.get("view", args.view)),
            "--resolution", str(settings.get("resolution", args.resolution)),
            "--background", args.background,
            "--lighting", str(settings.get("lighting", args.lighting)),
            "--texture-filter", "closest",
            "--material-permutation", args.material_permutation,
        ]
        if "model_rotation" in settings:
            command.extend(["--model-rotation", str(settings["model_rotation"])])
        if "ortho_scale" in settings:
            command.extend(["--ortho-scale", str(settings["ortho_scale"])])
        if "margin" in settings:
            command.extend(["--margin", str(settings["margin"])])
        if "bedrock_horizontal_uv_rotation" in settings:
            command.extend([
                "--bedrock-horizontal-uv-rotation",
                str(settings["bedrock_horizontal_uv_rotation"]),
            ])
        hide_bones = settings.get("hide_bones", [])
        if isinstance(hide_bones, str):
            hide_bones = [hide_bones]
        if isinstance(hide_bones, list):
            for bone in hide_bones:
                command.extend(["--hide-bone", str(bone)])
        bone_rotations = settings.get("bone_rotations", [])
        if isinstance(bone_rotations, str):
            bone_rotations = [bone_rotations]
        if isinstance(bone_rotations, list):
            for rotation in bone_rotations:
                command.extend(["--bone-rotation", str(rotation)])
        if args.blender:
            command.extend(["--blender", args.blender])
        if args.render_resolution:
            command.extend(["--render-resolution", args.render_resolution])
        render_jobs.append((identifier, destination, command))

    def render_one(job: tuple[str, Path, list[str]]) -> tuple[str, Path, subprocess.CompletedProcess[str]]:
        identifier, destination, command = job
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        return identifier, destination, completed

    failures: list[str] = []

    def report_result(
        completed_count: int,
        result: tuple[str, Path, subprocess.CompletedProcess[str]],
    ) -> None:
        identifier, destination, completed = result
        state = "ok" if completed.returncode == 0 else "FAILED"
        print(f"[{completed_count}/{len(render_jobs)}] {state}: {identifier} -> {destination.name}", flush=True)
        if completed.returncode:
            failures.append(identifier)
            if completed.stdout:
                print(completed.stdout, file=sys.stderr)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr)

    # Keep a one-job run on the main thread. Besides avoiding executor startup
    # overhead, this keeps Blender's Windows process environment identical to
    # invoking render_model.py directly. Multi-job batches still parallelize.
    if args.jobs == 1:
        for completed_count, job in enumerate(render_jobs, 1):
            report_result(completed_count, render_one(job))
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = [executor.submit(render_one, job) for job in render_jobs]
            for completed_count, future in enumerate(as_completed(futures), 1):
                report_result(completed_count, future.result())
    if failures:
        raise SystemExit("Failed renders: " + ", ".join(failures))
    override_note = f"; overrides {overrides_path}" if overrides_path.is_file() else ""
    print(f"Rendered {len(render_jobs)} blocks into {output}; skipped {skipped} existing files{override_note}")


if __name__ == "__main__":
    main()
