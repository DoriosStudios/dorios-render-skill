#!/usr/bin/env python3
"""Render obtainable vanilla Bedrock blocks as deterministic 80x80 PNG icons."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fnmatch
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from PIL import Image

from json_utils import load_jsonc


FACES = ("north", "south", "west", "east", "down", "up")
IMAGE_EXTENSIONS = (".tga", ".png", ".jpg", ".jpeg")
BLOCK_ID_ALIASES = {
    # blocks.json retains this legacy key while the item registry exposes the
    # modern identifier used by JEI and filenames.
    "grass": "grass_block",
}

# These blocks use flat/item sprites in Bedrock inventory. The optional JEI
# overrides file contributes its verified forceItemTexturePatterns and explicit
# texture overrides to this list at runtime.
ITEM_LIKE_PATTERNS = (
    "*_amethyst_bud",
    "*_boat",
    "*_bars",
    "*_bush",
    "*_chain",
    "*_door",
    "*_fern",
    "*_fungus",
    "*_hanging_sign",
    "*_lightning_rod",
    "*_minecart",
    "*_raft",
    "*_rail",
    "*_sapling",
    "*_sign",
    "*_torch",
    "*_vine",
    "*_vines",
    "*_coral",
    "*_coral_fan",
    "*_coral_wall_fan",
)
ITEM_LIKE_EXACT = {
    "amethyst_cluster", "azalea", "barrier", "big_dripleaf", "boat",
    "brewing_stand", "bush", "campfire", "chain", "crimson_roots",
    "dead_bush", "deadbush", "fern", "firefly_bush", "flower_pot",
    "flowering_azalea", "frame", "glow_frame", "ladder", "large_fern",
    "leaf_litter", "lever", "lightning_rod", "lily_pad",
    "mangrove_propagule", "minecart", "pointed_dripstone", "raft", "rail",
    "seagrass", "small_dripleaf", "spore_blossom", "sulfur_spike",
    "sculk_vein",
    "short_dry_grass", "short_grass", "soul_campfire", "tall_dry_grass",
    "tall_grass", "torch", "tripwire_hook", "twisting_vines", "vine",
    "warped_roots", "waterlily", "web", "weeping_vines",
}

# Keep these for screenshots or dedicated hand-authored geometry. Families are
# intentionally reported rather than approximated as misleading full cubes.
COMPLEX_PATTERNS = (
    "*_candle",
    "*_head",
    "*_lantern",
    "*_skull",
    "*_wall_head",
    "*_wall_skull",
    "*_copper_golem_statue",
    "*_egg",
)
COMPLEX_EXACT = {
    "anvil", "bell", "calibrated_sculk_sensor", "candle", "cauldron",
    "chipped_anvil", "comparator", "composter", "conduit",
    "damaged_anvil", "decorated_pot", "dragon_egg",
    "end_rod", "frog_spawn", "grindstone", "hopper", "lectern",
    "lantern", "repeater", "scaffolding",
    "sculk_sensor", "sculk_shrieker", "sea_pickle", "sniffer_egg",
    "stonecutter", "turtle_egg", "dried_ghast",
}

# Internal/technical blocks intentionally lack a usable inventory texture and
# should not be mistaken for renderer failures or screenshot candidates.
TECHNICAL_PATTERNS = (
    "light_block_*",
)

# A few inventory definitions intentionally differ from the placed block. Most
# carried textures are correct (foliage tint, horizontal dispenser/dropper,
# piston top), but the portal frame's carried key represents its eye overlay
# rather than the base item model.
PLACED_TEXTURE_IDS = {
    "end_portal_frame",
}

# A fixed camera scale preserves the relative size of partial blocks. This is
# equivalent to the automatic framing of a full 16x16x16 cube with the vanilla
# isometric camera, but does not enlarge buttons or the heavy core.
VANILLA_ORTHO_SCALE = 25.5
FLOOR_ANCHORED_SHAPES = {"trapdoor", "carpet", "pressure_plate"}
FLOOR_SHAPE_OFFSET_Y = 17
INVENTORY_MODELS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "vanilla_inventory_models"
INVENTORY_GEOMETRIES = {
    "fence": ("fence_inventory.geo.json", "geometry.fence_inventory"),
    "fence_gate": ("fence_gate_inventory.geo.json", "geometry.fence_gate_inventory"),
    "wall": ("wall_inventory.geo.json", "geometry.wall_inventory"),
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overrides", type=Path, help="JEI recipe-overrides.json")
    parser.add_argument("--renderer", type=Path, help="render_model.py path")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--prune-stale", action="store_true")
    parser.add_argument("--classify-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include", help="Comma-separated block identifiers")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--blender")
    return parser.parse_args()


def short_identifier(value: str) -> str:
    return value.split("|", 1)[0].split(":")[-1]


def matches(value: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def first_texture(value: Any) -> tuple[str, str | None] | None:
    if isinstance(value, list):
        for entry in value:
            resolved = first_texture(entry)
            if resolved:
                return resolved
        return None
    if isinstance(value, str):
        return value, None
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str):
            tint = value.get("overlay_color")
            return path, tint if isinstance(tint, str) else None
    return None


def texture_file(resource_pack: Path, logical: str) -> Path | None:
    relative = logical.replace("\\", "/").strip("/")
    candidate = resource_pack / relative
    if candidate.suffix.lower() in IMAGE_EXTENSIONS and candidate.is_file():
        return candidate.resolve()
    for extension in IMAGE_EXTENSIONS:
        with_extension = candidate.with_suffix(extension)
        if with_extension.is_file():
            return with_extension.resolve()
    return None


def block_face_keys(identifier: str, definition: dict[str, Any]) -> dict[str, str] | None:
    # Bedrock uses carried_textures for inventory rendering when available.
    # Falling back to placed textures makes foliage gray and can select the
    # wrong state for directional blocks.
    textures = (
        definition.get("textures")
        if identifier in PLACED_TEXTURE_IDS
        else definition.get("carried_textures") or definition.get("textures")
    )
    if isinstance(textures, str):
        return {face: textures for face in FACES}
    if not isinstance(textures, dict):
        return None
    fallback = textures.get("side") or textures.get("up") or textures.get("down")
    if not isinstance(fallback, str):
        fallback = next((value for value in textures.values() if isinstance(value, str)), None)
    if not fallback:
        return None
    return {
        face: str(
            textures.get(face)
            or (textures.get("side") if face in {"north", "south", "west", "east"} else None)
            or fallback
        )
        for face in FACES
    }


def resolve_faces(
    identifier: str,
    definition: dict[str, Any],
    terrain: dict[str, Any],
    resource_pack: Path,
) -> tuple[dict[str, dict[str, Any]] | None, list[Path], list[str]]:
    keys = block_face_keys(identifier, definition)
    if not keys:
        return None, [], ["missing block texture mapping"]
    faces: dict[str, dict[str, Any]] = {}
    files: list[Path] = []
    errors: list[str] = []
    texture_data = terrain.get("texture_data", {})
    for face, key in keys.items():
        atlas_entry = texture_data.get(key)
        resolved = first_texture(atlas_entry.get("textures")) if isinstance(atlas_entry, dict) else None
        if not resolved:
            errors.append(f"{face}: unknown terrain key {key}")
            continue
        logical, tint = resolved
        file = texture_file(resource_pack, logical)
        if not file:
            errors.append(f"{face}: missing image {logical}")
            continue
        faces[face] = {"texture": logical, **({"tint": tint} if tint else {})}
        with Image.open(file) as image:
            if image.size != (16, 16):
                faces[face]["texture_size"] = list(image.size)
        if file not in files:
            files.append(file)
    return (faces if len(faces) == len(FACES) else None), files, errors


def face_uv(face: str, lower: tuple[float, float, float], upper: tuple[float, float, float]) -> list[float]:
    x0, y0, z0 = lower
    x1, y1, z1 = upper
    if face in {"up", "down"}:
        return [x0, z0, x1, z1]
    if face in {"north", "south"}:
        return [x0, 16 - y1, x1, 16 - y0]
    return [z0, 16 - y1, z1, 16 - y0]


def box(
    lower: tuple[float, float, float],
    upper: tuple[float, float, float],
    faces: dict[str, dict[str, Any]],
    full_uv: bool = False,
) -> dict[str, Any]:
    return {
        "from": list(lower),
        "to": list(upper),
        "faces": {
            face: {
                **spec,
                "uv": spec.get(
                    "uv",
                    [0, 0, 16, 16] if full_uv else face_uv(face, lower, upper),
                ),
            }
            for face, spec in faces.items()
        },
    }


def shape_for(identifier: str) -> str:
    if identifier == "end_portal_frame":
        return "end_portal_frame"
    if identifier == "enchanting_table":
        return "enchanting_table"
    if identifier == "heavy_core":
        return "heavy_core"
    if identifier.endswith("_stairs"):
        return "stairs"
    if identifier.endswith("_slab"):
        return "slab"
    if identifier == "fence_gate" or identifier.endswith("_fence_gate"):
        return "fence_gate"
    if identifier == "fence" or identifier.endswith("_fence"):
        return "fence"
    if identifier.endswith("_wall"):
        return "wall"
    if identifier.endswith("_button"):
        return "button"
    if identifier.endswith("_pressure_plate"):
        return "pressure_plate"
    if identifier.endswith("_carpet"):
        return "carpet"
    if identifier == "trapdoor" or identifier.endswith("_trapdoor"):
        return "trapdoor"
    if identifier.endswith("_pane") or identifier in {"iron_bars", "copper_bars"}:
        return "pane"
    if identifier == "cactus":
        return "cactus"
    if identifier in {"dirt_path", "grass_path", "farmland"}:
        return "short_block"
    if identifier in {"daylight_detector"}:
        return "detector"
    if identifier in {"snow_layer"}:
        return "snow_layer"
    return "cube"


def shape_boxes(shape: str) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    shapes = {
        "cube": [((0, 0, 0), (16, 16, 16))],
        "slab": [((0, 0, 0), (16, 8, 16))],
        "stairs": [((0, 0, 0), (16, 8, 16)), ((0, 8, 8), (16, 16, 16))],
        "fence": [
            ((6, 0, 6), (10, 16, 10)),
            ((7, 6, 0), (9, 9, 16)),
            ((7, 12, 0), (9, 15, 16)),
        ],
        "fence_gate": [
            ((6, 0, 1), (10, 16, 4)), ((6, 0, 12), (10, 16, 15)),
            ((7, 5, 4), (9, 8, 12)), ((7, 11, 4), (9, 14, 12)),
        ],
        "wall": [
            ((5, 0, 5), (11, 16, 11)),
            ((0, 4, 6), (16, 13, 10)),
        ],
        "button": [((5, 0, 6), (11, 2, 10))],
        "pressure_plate": [((1, 0, 1), (15, 1, 15))],
        "carpet": [((0, 0, 0), (16, 1, 16))],
        "trapdoor": [((0, 0, 0), (16, 3, 16))],
        "pane": [((7, 0, 0), (9, 16, 16))],
        "cactus": [((1, 0, 1), (15, 16, 15))],
        "short_block": [((0, 0, 0), (16, 15, 16))],
        "detector": [((0, 0, 0), (16, 6, 16))],
        "snow_layer": [((0, 0, 0), (16, 2, 16))],
        "end_portal_frame": [((0, 0, 0), (16, 13, 16))],
        "enchanting_table": [((0, 0, 0), (16, 12, 16))],
        "heavy_core": [((4, 0, 4), (12, 8, 12))],
    }
    return shapes[shape]


def is_shulker_box(identifier: str) -> bool:
    return identifier == "shulker_box" or identifier.endswith("_shulker_box")


def shulker_entity_texture(identifier: str, resource_pack: Path) -> Path:
    color = "white" if identifier == "shulker_box" else identifier.removesuffix("_shulker_box")
    if color == "light_gray":
        color = "silver"
    logical = f"textures/entity/shulker/shulker_{color}"
    file = texture_file(resource_pack, logical)
    if not file:
        raise RuntimeError(f"Missing stable shulker entity texture: {logical}")
    return file


def heavy_core_atlas_faces(
    faces: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result = {name: dict(spec) for name, spec in faces.items()}
    for face in ("north", "south", "west", "east"):
        result[face]["uv"] = [0, 8, 8, 16]
        result[face]["texture_size"] = [16, 16]
    result["up"]["uv"] = [0, 0, 8, 8]
    result["up"]["texture_size"] = [16, 16]
    result["down"]["uv"] = [8, 0, 16, 8]
    result["down"]["texture_size"] = [16, 16]
    return result


def orient_inventory_faces(
    identifier: str,
    shape: str,
    faces: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if shape == "pane":
        fill = dict(faces["north"])
        edge = dict(faces["east"])
        oriented = {
            "north": dict(edge),
            "south": dict(edge),
            "west": dict(fill),
            "east": dict(fill),
            "down": dict(edge),
            "up": dict(edge),
        }
    else:
        # Bedrock's inventory front is authored on south. In the approved
        # iso-ne view that face lands on the left, while vanilla inventory
        # icons present the directional/front face on the right. The renderer's
        # Bedrock-to-Blender conversion flips Z, so this requires a half turn
        # of the horizontal texture assignment.
        oriented = {
            "north": dict(faces["south"]),
            "south": dict(faces["north"]),
            "west": dict(faces["east"]),
            "east": dict(faces["west"]),
            "down": dict(faces["down"]),
            "up": dict(faces["up"]),
        }
    if identifier in {"piston", "sticky_piston"}:
        oriented["up"]["uv_rotation"] = (int(oriented["up"].get("uv_rotation", 0)) + 90) % 360
    return oriented


def model_document(identifier: str, faces: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    shape = shape_for(identifier)
    if identifier == "heavy_core":
        faces = heavy_core_atlas_faces(faces)
    faces = orient_inventory_faces(identifier, shape, faces)
    elements = [
            box(lower, upper, faces, full_uv=shape == "heavy_core")
            for lower, upper in shape_boxes(shape)
    ]
    return shape, {"elements": elements}


def shift_png_down(path: Path, offset_y: int) -> None:
    with Image.open(path) as source:
        image = source.convert("RGBA")
    shifted = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shifted.alpha_composite(image, (0, offset_y))
    shifted.save(path)


def main() -> None:
    args = arguments()
    if args.jobs < 1:
        raise SystemExit("--jobs must be at least 1")
    source = args.source_root.expanduser().resolve()
    resource_pack = source / "resource_pack"
    blocks_path = resource_pack / "blocks.json"
    terrain_path = resource_pack / "textures" / "terrain_texture.json"
    items_path = source / "metadata" / "vanilladata_modules" / "mojang-items.json"
    item_texture_path = resource_pack / "textures" / "item_texture.json"
    for required in (blocks_path, terrain_path, items_path, item_texture_path):
        if not required.is_file():
            raise SystemExit(f"Missing stable Bedrock source: {required}")

    blocks = load_jsonc(blocks_path)
    terrain = load_jsonc(terrain_path)
    items = load_jsonc(items_path).get("data_items", [])
    item_ids = {short_identifier(str(item.get("name", ""))) for item in items}
    item_texture_ids = set(load_jsonc(item_texture_path).get("texture_data", {}))
    item_patterns = list(ITEM_LIKE_PATTERNS)
    forced_item_ids: set[str] = set()
    if args.overrides:
        overrides = load_jsonc(args.overrides.expanduser().resolve())
        item_patterns.extend(str(value) for value in overrides.get("forceItemTexturePatterns", []))
        forced_item_ids.update(short_identifier(key) for key in overrides.get("textures", {}))

    requested = {
        short_identifier(value.strip())
        for value in (args.include or "").split(",")
        if value.strip()
    }
    block_sources = {
        identifier: identifier
        for identifier in blocks
        if identifier in item_ids
    }
    for block_identifier, item_identifier in BLOCK_ID_ALIASES.items():
        if block_identifier in blocks and item_identifier in item_ids:
            block_sources[item_identifier] = block_identifier
    candidates = sorted(
        identifier for identifier in block_sources
        if not requested or identifier in requested
    )
    if args.limit is not None:
        candidates = candidates[:args.limit]

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = args.report.expanduser().resolve() if args.report else output / "render-report.json"
    renderer = args.renderer.expanduser().resolve() if args.renderer else Path(__file__).with_name("render_model.py")
    if not renderer.is_file():
        raise SystemExit(f"Renderer not found: {renderer}")

    report: dict[str, Any] = {
        "source": {
            "root": str(source),
            "version": load_jsonc(source / "version.json").get("latest", {}).get("version")
            if (source / "version.json").is_file() else None,
        },
        "resolution": "80x80",
        "lighting": "vanilla",
        "candidates": len(candidates),
        "rendered": [],
        "existing": [],
        "itemLike": [],
        "complexGeometry": [],
        "technical": [],
        "pruned": [],
        "unresolved": [],
        "failed": [],
        "shapeCounts": {},
        "shapes": {},
    }
    jobs: list[dict[str, Any]] = []
    models_directory = tempfile.TemporaryDirectory(prefix="dorios-vanilla-block-models-")
    models_root = Path(models_directory.name)
    def prune_output(identifier: str, destination: Path, record: bool = True) -> None:
        if args.prune_stale and destination.is_file():
            destination.unlink()
            if record:
                report["pruned"].append(identifier)

    try:
        for identifier in candidates:
            destination = output / f"{identifier}.png"
            definition = blocks.get(block_sources[identifier])
            if not isinstance(definition, dict):
                report["unresolved"].append({"id": identifier, "errors": ["invalid block definition"]})
                prune_output(identifier, destination)
                continue
            if identifier in COMPLEX_EXACT or matches(identifier, COMPLEX_PATTERNS):
                report["complexGeometry"].append(identifier)
                prune_output(identifier, destination)
                continue
            if matches(identifier, TECHNICAL_PATTERNS):
                report["technical"].append(identifier)
                prune_output(identifier, destination)
                continue
            if (
                identifier in ITEM_LIKE_EXACT
                or identifier in forced_item_ids
                or identifier in item_texture_ids
                or matches(identifier, item_patterns)
            ):
                report["itemLike"].append(identifier)
                prune_output(identifier, destination)
                continue
            if is_shulker_box(identifier):
                geometry_path = resource_pack / "models" / "entity" / "shulker_v1.0.geo.json"
                if not geometry_path.is_file():
                    report["unresolved"].append({
                        "id": identifier,
                        "errors": [f"missing stable geometry {geometry_path}"],
                    })
                    prune_output(identifier, destination)
                    continue
                entity_texture = shulker_entity_texture(identifier, resource_pack)
                shape = "shulker_v1.0"
                report["shapeCounts"][shape] = report["shapeCounts"].get(shape, 0) + 1
                report["shapes"][identifier] = shape
                if args.skip_existing and destination.is_file():
                    report["existing"].append(identifier)
                    continue
                if not args.classify_only:
                    prune_output(identifier, destination, record=False)
                command = [
                    sys.executable, str(renderer),
                    "--model", str(geometry_path),
                    "--textures", str(entity_texture),
                    "--geometry", "geometry.shulker",
                    "--hide-bone", "head",
                    "--bone-scale", "base=0.998,1,0.998",
                    "--output", str(destination),
                    "--resolution", "80x80",
                    "--lighting", "vanilla",
                    "--ortho-scale", str(VANILLA_ORTHO_SCALE),
                    "--texture-filter", "closest",
                ]
                if args.blender:
                    command.extend(["--blender", args.blender])
                jobs.append({"id": identifier, "output": destination, "command": command})
                continue
            faces, texture_files, errors = resolve_faces(identifier, definition, terrain, resource_pack)
            if not faces:
                report["unresolved"].append({"id": identifier, "errors": errors})
                prune_output(identifier, destination)
                continue
            base_shape = shape_for(identifier)
            inventory_geometry = INVENTORY_GEOMETRIES.get(base_shape)
            if inventory_geometry:
                model_name, geometry_identifier = inventory_geometry
                inventory_model = INVENTORY_MODELS_ROOT / model_name
                if not inventory_model.is_file():
                    report["unresolved"].append({
                        "id": identifier,
                        "errors": [f"missing inventory geometry {inventory_model}"],
                    })
                    prune_output(identifier, destination)
                    continue
                shape = f"{base_shape}_inventory"
                report["shapeCounts"][shape] = report["shapeCounts"].get(shape, 0) + 1
                report["shapes"][identifier] = shape
                if args.skip_existing and destination.is_file():
                    report["existing"].append(identifier)
                    continue
                if not args.classify_only:
                    prune_output(identifier, destination, record=False)
                command = [
                    sys.executable, str(renderer),
                    "--model", str(inventory_model),
                    "--textures", str(texture_files[0]),
                    "--geometry", geometry_identifier,
                    "--output", str(destination),
                    "--resolution", "80x80",
                    "--lighting", "vanilla",
                    "--ortho-scale", str(VANILLA_ORTHO_SCALE),
                    "--texture-filter", "closest",
                ]
                if args.blender:
                    command.extend(["--blender", args.blender])
                jobs.append({"id": identifier, "output": destination, "command": command})
                continue
            shape, model = model_document(identifier, faces)
            report["shapeCounts"][shape] = report["shapeCounts"].get(shape, 0) + 1
            report["shapes"][identifier] = shape
            if args.skip_existing and destination.is_file():
                report["existing"].append(identifier)
                continue
            if not args.classify_only:
                prune_output(identifier, destination, record=False)
            model_path = models_root / f"{identifier}.json"
            model_path.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
            command = [
                sys.executable, str(renderer),
                "--model", str(model_path),
                "--textures", *(str(path) for path in texture_files),
                "--output", str(destination),
                "--resolution", "80x80",
                "--lighting", "vanilla",
                "--ortho-scale", str(VANILLA_ORTHO_SCALE),
                "--texture-filter", "closest",
            ]
            if args.blender:
                command.extend(["--blender", args.blender])
            jobs.append({
                "id": identifier,
                "output": destination,
                "command": command,
                "shiftY": FLOOR_SHAPE_OFFSET_Y if shape in FLOOR_ANCHORED_SHAPES else 0,
            })

        if not args.classify_only:
            def render(job: dict[str, Any]) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
                completed = subprocess.run(job["command"], capture_output=True, text=True, check=False)
                if completed.returncode == 0 and job["output"].is_file() and job.get("shiftY"):
                    shift_png_down(job["output"], int(job["shiftY"]))
                return job, completed

            with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                futures = [executor.submit(render, job) for job in jobs]
                for completed_count, future in enumerate(as_completed(futures), 1):
                    job, completed = future.result()
                    identifier = job["id"]
                    if completed.returncode == 0 and job["output"].is_file():
                        report["rendered"].append(identifier)
                    else:
                        report["failed"].append({
                            "id": identifier,
                            "stdout": completed.stdout[-2000:],
                            "stderr": completed.stderr[-2000:],
                        })
                    if completed_count % 25 == 0 or completed_count == len(jobs):
                        print(
                            f"[{completed_count}/{len(jobs)}] rendered={len(report['rendered'])} "
                            f"failed={len(report['failed'])}",
                            flush=True,
                        )
    finally:
        models_directory.cleanup()

    for key in ("rendered", "existing", "itemLike", "complexGeometry", "technical", "pruned"):
        report[key] = sorted(report[key])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"Candidates={report['candidates']} rendered={len(report['rendered'])} "
        f"existing={len(report['existing'])} item-like={len(report['itemLike'])} "
        f"complex={len(report['complexGeometry'])} technical={len(report['technical'])} "
        f"pruned={len(report['pruned'])} "
        f"unresolved={len(report['unresolved'])} "
        f"failed={len(report['failed'])}"
    )
    print(f"Report: {report_path}")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
