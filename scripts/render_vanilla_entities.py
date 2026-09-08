#!/usr/bin/env python3
"""Render one canonical image for every vanilla Bedrock client entity."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

from PIL import Image

from json_utils import load_jsonc


IMAGE_EXTENSIONS = (".tga", ".png", ".jpg", ".jpeg")
SECONDARY_TEXTURE_WORDS = (
    "angry", "armor", "baby", "charged", "collar", "emissive", "eyes",
    "glow", "layer", "overlay", "saddle", "sheared", "tame",
)
POSE_ALIAS_PRIORITY = (
    "idle", "walk", "move", "fly", "flying", "swim", "flap", "hover",
    "general", "rotate", "bite",
)

# A catalog render should look like the mob standing still in-game. Most
# Bedrock geometries already store that pose, so only entities whose bind pose
# is an assembly/setup pose need animation sampling here.
CURATED_POSE_ANIMATIONS: dict[str, tuple[str, ...]] = {
    "blaze": ("animation.blaze.move",),
    "breeze": ("animation.breeze.idle",),
    "camel": ("animation.camel.idle",),
    "camel_husk": ("animation.camel.idle",),
    "cat": ("animation.ocelot.walk",),
    "cave_spider": ("animation.spider.default_leg_pose",),
    "elder_guardian": (
        "animation.guardian.setup",
        "animation.guardian.spikes",
        "animation.guardian.swim",
        "animation.guardian.move_eye",
    ),
    "ender_dragon": ("animation.ender_dragon.wings_limbs_movement",),
    "enderman": ("animation.enderman.base_pose", "animation.enderman.arms_legs"),
    "evocation_illager": ("animation.evoker.general",),
    "glow_squid": ("animation.squid.move",),
    "guardian": (
        "animation.guardian.setup",
        "animation.guardian.spikes",
        "animation.guardian.swim",
        "animation.guardian.move_eye",
    ),
    "villager": ("animation.villager.general",),
    "villager_v2": ("animation.villager.general",),
    "wandering_trader": ("animation.villager.general",),
    "witch": ("animation.villager.general", "animation.witch.general"),
    "ocelot": ("animation.ocelot.walk",),
    "shulker": ("animation.shulker.move",),
    "spider": ("animation.spider.default_leg_pose",),
    "trader_llama": ("animation.llama.setup",),
    "turtle": ("animation.turtle.general",),
}
CURATED_MOLANG_CONTEXT: dict[str, dict[str, float]] = {
    "cat": {"query.modified_move_speed": 0.0},
    "ocelot": {"query.modified_move_speed": 0.0},
    "guardian": {
        "variable.spike_extension": 0.55,
        "variable.spike_shake": 0.0,
        "variable.tail_base_angle": 0.0,
    },
    "elder_guardian": {
        "variable.spike_extension": 0.55,
        "variable.spike_shake": 0.0,
        "variable.tail_base_angle": 0.0,
    },
}
CURATED_POSITION_THIS_PIVOT_BONES: dict[str, set[str]] = {
    "guardian": {"eye", *(f"spikepart{index}" for index in range(12))},
    "elder_guardian": {"eye", *(f"spikepart{index}" for index in range(12))},
}
CURATED_TEXTURE_KEYS = {
    "elder_guardian": "elder",
    "ocelot": "wild",
}
CURATED_HIDDEN_BONES: dict[str, tuple[str, ...]] = {
    "donkey": (
        "EarL", "EarR", "Saddle", "BitL", "BitR", "Bridle",
        "ReinsL", "ReinsR", "BagL", "BagR",
    ),
    "skeleton_horse": (
        "MuleEarL", "MuleEarR", "Saddle", "BitL", "BitR", "Bridle",
        "ReinsL", "ReinsR", "BagL", "BagR",
    ),
    "trader_llama": ("chest1", "chest2"),
}
CURATED_MODEL_ROTATIONS: dict[str, str] = {}
CURATED_BIND_POSE_ROTATIONS: dict[str, dict[str, list[float]]] = {
    # Feline bodies are authored vertically in the published geometry. The
    # runtime rotates only the torso cube, while the child pivots already use
    # their final standing coordinates.
    "cat": {"body": [90.0, 0.0, 0.0]},
    "llama": {"body": [90.0, 0.0, 0.0]},
    "ocelot": {"body": [90.0, 0.0, 0.0]},
    "polar_bear": {"body": [90.0, 0.0, 0.0]},
    "trader_llama": {"body": [90.0, 0.0, 0.0]},
    # The adult villager brim is authored as a vertical plane. The equivalent
    # zombie-villager geometry includes this missing bind-pose rotation.
    "villager_v2": {"brim": [-90.0, 0.0, 0.0]},
}
OPAQUE_VISIBLE_TEXTURES = {"blaze", "enderman", "glow_squid", "sheep"}
BREEZE_RENDER_LAYERS = (
    ("body", "default", "default"),
    ("eyes", "breeze_eyes", "breeze_eyes"),
    ("wind_top", "breeze_wind_top", "breeze_wind"),
    ("wind_mid", "breeze_wind_mid", "breeze_wind"),
    ("wind_bottom", "breeze_wind_bottom", "breeze_wind"),
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path, help="Mojang bedrock-samples checkout")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Defaults to <output>/render-report.json")
    parser.add_argument("--view", default="iso-ne")
    parser.add_argument("--resolution", default="80x80")
    parser.add_argument("--render-resolution", help="High-resolution Blender source, such as 1024x1024")
    parser.add_argument("--background", default="transparent")
    parser.add_argument(
        "--lighting",
        choices=["vanilla", "balanced", "left_light", "right_light", "studio", "flat", "dramatic", "neon"],
        default="vanilla",
    )
    parser.add_argument("--jobs", type=int, default=1, help="Concurrent Blender processes")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--catalog",
        choices=["mobs", "all"],
        default="mobs",
        help="Render spawn-egg mobs only, or every client entity including technical entities",
    )
    parser.add_argument(
        "--pose",
        choices=["curated", "auto", "bind"],
        default="curated",
        help="Use a curated idle pose, automatically select an animation, or keep the bind pose",
    )
    parser.add_argument("--pose-time", type=float, default=0.0, help="Animation time sampled by the automatic pose")
    parser.add_argument("--classify-only", action="store_true", help="Resolve assets and write the report without rendering")
    parser.add_argument("--include", help="Comma-separated entity identifiers for a focused run")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--renderer", type=Path)
    parser.add_argument("--blender")
    return parser.parse_args()


def short_identifier(value: str) -> str:
    return value.split(":", 1)[-1]


def definition_rank(path: Path, identifier: str) -> tuple[int, int, str]:
    short = short_identifier(identifier).casefold()
    name = path.name.casefold()
    if name == f"{short}.entity.json":
        return 10_000, 0, name
    version = re.search(r"(?:[._-]v)(\d+)(?:[._-](\d+))?", name)
    if version:
        major = int(version.group(1))
        minor = int(version.group(2) or 0)
        return 1_000 + major, minor, name
    return 100, 0, name


def client_description(path: Path) -> dict[str, Any]:
    document = load_jsonc(path)
    description = document.get("minecraft:client_entity", {}).get("description")
    if not isinstance(description, dict):
        raise ValueError("missing minecraft:client_entity.description")
    return description


def collect_definitions(entity_root: Path) -> tuple[dict[str, tuple[Path, dict[str, Any]]], dict[str, list[str]], list[dict[str, str]]]:
    grouped: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    invalid: list[dict[str, str]] = []
    for path in sorted(entity_root.glob("*.entity.json")):
        try:
            description = client_description(path)
            identifier = str(description["identifier"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            invalid.append({"file": str(path), "error": str(exc)})
            continue
        grouped[identifier].append((path, description))

    selected: dict[str, tuple[Path, dict[str, Any]]] = {}
    alternatives: dict[str, list[str]] = {}
    for identifier, definitions in grouped.items():
        ordered = sorted(
            definitions,
            key=lambda item: definition_rank(item[0], identifier),
            reverse=True,
        )
        selected[identifier] = ordered[0]
        alternatives[identifier] = [path.name for path, _ in ordered[1:]]
    return selected, alternatives, invalid


def geometry_documents(path: Path) -> list[dict[str, Any]]:
    document = load_jsonc(path)
    modern = document.get("minecraft:geometry")
    result: list[dict[str, Any]] = []
    if isinstance(modern, list):
        for geometry in modern:
            if not isinstance(geometry, dict):
                continue
            identifier = geometry.get("description", {}).get("identifier")
            if isinstance(identifier, str):
                result.append({
                    "identifier": identifier,
                    "selection": identifier,
                    "parent": None,
                    "geometry": geometry,
                    "path": path,
                    "legacy": False,
                })
    for selection, geometry in document.items():
        if not isinstance(selection, str) or not selection.casefold().startswith("geometry.") or not isinstance(geometry, dict):
            continue
        identifier, separator, parent = selection.partition(":")
        result.append({
            "identifier": identifier,
            "selection": selection,
            "parent": parent if separator else None,
            "geometry": geometry,
            "path": path,
            "legacy": True,
        })
    return result


def geometry_index(models_root: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid: list[dict[str, str]] = []
    for path in sorted(models_root.rglob("*.json")):
        try:
            entries = geometry_documents(path)
        except (OSError, ValueError, TypeError) as exc:
            invalid.append({"file": str(path), "error": str(exc)})
            continue
        for entry in entries:
            index[str(entry["identifier"]).casefold()].append(entry)
    return index, invalid


def animation_index(animations_root: Path) -> tuple[dict[str, tuple[Path, dict[str, Any]]], list[dict[str, str]]]:
    index: dict[str, tuple[Path, dict[str, Any]]] = {}
    invalid: list[dict[str, str]] = []
    for path in sorted(animations_root.rglob("*.json")):
        try:
            animations = load_jsonc(path).get("animations", {})
        except (OSError, ValueError, TypeError) as exc:
            invalid.append({"file": str(path), "error": str(exc)})
            continue
        if not isinstance(animations, dict):
            continue
        for identifier, animation in animations.items():
            if isinstance(identifier, str) and isinstance(animation, dict):
                index[identifier] = (path, animation)
    return index, invalid


def pose_alias_score(alias: str) -> int:
    folded = alias.casefold()
    if folded in POSE_ALIAS_PRIORITY:
        return POSE_ALIAS_PRIORITY.index(folded)
    tokens = [token for token in re.split(r"[^a-z0-9]+", folded) if token]
    for preferred in ("idle", "walk", "fly", "flying", "swim", "flap", "hover"):
        if preferred in tokens:
            return POSE_ALIAS_PRIORITY.index(preferred) + 1
    if "move" in tokens:
        return 20 + POSE_ALIAS_PRIORITY.index("move")
    if folded.endswith("movement"):
        return 60
    return 100


def choose_pose_animations(
    description: dict[str, Any],
    index: dict[str, tuple[Path, dict[str, Any]]],
) -> list[dict[str, Any]]:
    mappings = description.get("animations", {})
    if not isinstance(mappings, dict):
        return []
    available: list[dict[str, Any]] = []
    for order, (alias, identifier) in enumerate(mappings.items()):
        if not isinstance(identifier, str) or identifier not in index:
            continue
        path, animation = index[identifier]
        available.append({
            "alias": str(alias),
            "identifier": identifier,
            "path": path,
            "animation": animation,
            "order": order,
        })
    chosen = [entry for entry in available if entry["alias"].casefold() == "setup"]
    functional = [entry for entry in available if entry["alias"].casefold() != "setup"]
    if functional:
        best_score = min(pose_alias_score(entry["alias"]) for entry in functional)
        if best_score < 60:
            functional_choice = min(
                (entry for entry in functional if pose_alias_score(entry["alias"]) == best_score),
                key=lambda entry: entry["order"],
            )
            chosen.append(functional_choice)
            if "walk" in re.split(r"[^a-z0-9]+", functional_choice["alias"].casefold()):
                chosen.extend(
                    entry for entry in functional
                    if entry["alias"].casefold() == "move"
                )
        elif best_score == 60:
            chosen.extend(
                entry for entry in functional
                if pose_alias_score(entry["alias"]) == 60
            )
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in chosen:
        if entry["identifier"] not in seen:
            seen.add(entry["identifier"])
            unique.append(entry)
    return unique


def choose_curated_pose_animations(
    entity_name: str,
    index: dict[str, tuple[Path, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    chosen: list[dict[str, Any]] = []
    warnings: list[str] = []
    for order, identifier in enumerate(CURATED_POSE_ANIMATIONS.get(entity_name, ())):
        indexed = index.get(identifier)
        if not indexed:
            warnings.append(f"curated animation {identifier!r} was not found")
            continue
        path, animation = indexed
        chosen.append({
            "alias": "curated",
            "identifier": identifier,
            "path": path,
            "animation": animation,
            "order": order,
        })
    return chosen, warnings


def convert_ternary(expression: str) -> str:
    rebuilt: list[str] = []
    index = 0
    while index < len(expression):
        if expression[index] != "(":
            rebuilt.append(expression[index])
            index += 1
            continue
        depth = 1
        end = index + 1
        while end < len(expression) and depth:
            if expression[end] == "(":
                depth += 1
            elif expression[end] == ")":
                depth -= 1
            end += 1
        if depth:
            rebuilt.append(expression[index:])
            break
        rebuilt.append("(" + convert_ternary(expression[index + 1:end - 1]) + ")")
        index = end
    value = "".join(rebuilt)
    depth = 0
    question = None
    for position, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "?" and depth == 0:
            if position + 1 < len(value) and value[position + 1] == "?":
                continue
            question = position
            break
    if question is None:
        return value
    nested = 0
    depth = 0
    colon = None
    for position in range(question + 1, len(value)):
        character = value[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and character == "?":
            nested += 1
        elif depth == 0 and character == ":":
            if nested:
                nested -= 1
            else:
                colon = position
                break
    if colon is None:
        return value
    condition = convert_ternary(value[:question])
    when_true = convert_ternary(value[question + 1:colon])
    when_false = convert_ternary(value[colon + 1:])
    return f"(({when_true}) if ({condition}) else ({when_false}))"


def sin_degrees(value: float) -> float:
    return math.sin(math.radians(value))


def cos_degrees(value: float) -> float:
    return math.cos(math.radians(value))


MOLANG_FUNCTIONS = {
    "sin_d": sin_degrees,
    "cos_d": cos_degrees,
    "abs_v": abs,
    "sqrt_v": math.sqrt,
    "pow_v": pow,
    "floor_v": math.floor,
    "ceil_v": math.ceil,
    "round_v": round,
    "min_v": min,
    "max_v": max,
    "mod_v": lambda left, right: left % right,
    "clamp_v": lambda value, lower, upper: max(lower, min(upper, value)),
    "lerp_v": lambda start, end, amount: start + (end - start) * amount,
    "lerprotate_v": lambda start, end, amount: start + (end - start) * amount,
    "pi_v": math.pi,
}
ALLOWED_EXPRESSION_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.Name, ast.Load, ast.Constant, ast.Add, ast.Sub, ast.Mult,
    ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def molang_value(
    expression: Any,
    this_value: float,
    time: float,
    context_overrides: dict[str, float] | None = None,
) -> float:
    if isinstance(expression, (int, float)):
        return float(expression)
    if not isinstance(expression, str):
        raise ValueError(f"unsupported Molang value {expression!r}")
    value = expression.strip().rstrip(";").casefold()
    replacements = {
        "math.sin": "sin_d", "math.cos": "cos_d", "math.abs": "abs_v",
        "math.sqrt": "sqrt_v", "math.pow": "pow_v", "math.floor": "floor_v",
        "math.ceil": "ceil_v", "math.round": "round_v", "math.min": "min_v",
        "math.max": "max_v", "math.mod": "mod_v", "math.clamp": "clamp_v",
        "math.lerp": "lerp_v", "math.lerprotate": "lerprotate_v",
        "math.pi": "pi_v",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    context = {
        "query.anim_time": time,
        "query.life_time": time,
        "query.modified_distance_moved": time,
        "query.modified_move_speed": 1.0,
        "query.model_scale": 1.0,
        "query.ground_speed": 1.0,
        "query.is_moving": 1.0,
        "query.is_on_ground": 1.0,
        "query.standing_scale": 0.0,
        "query.target_x_rotation": 0.0,
        "query.target_y_rotation": 0.0,
    }
    if context_overrides:
        context.update({key.casefold(): float(value) for key, value in context_overrides.items()})

    def replace_context(match: re.Match[str]) -> str:
        token = match.group(0).casefold()
        token = re.sub(r"^(q|v|t|c)\.", lambda item: {
            "q.": "query.", "v.": "variable.", "t.": "temp.", "c.": "context.",
        }[item.group(0)], token)
        return repr(float(context.get(token, 0.0)))

    value = re.sub(
        r"\b(?:query|q|variable|v|temp|t|context|c)\.[a-z_][a-z0-9_.]*(?:\([^()]*\))?",
        replace_context,
        value,
    )
    value = re.sub(r"\bthis\b", repr(float(this_value)), value)
    value = re.sub(r"(?<=\d)f\b", "", value)
    value = value.replace("&&", " and ").replace("||", " or ")
    value = re.sub(r"!(?!=)", " not ", value)
    value = re.sub(r"\btrue\b", "1.0", value)
    value = re.sub(r"\bfalse\b", "0.0", value)
    value = convert_ternary(value)
    parsed = ast.parse(value, mode="eval")
    if any(not isinstance(node, ALLOWED_EXPRESSION_NODES) for node in ast.walk(parsed)):
        raise ValueError(f"unsupported Molang expression {expression!r}")
    for node in ast.walk(parsed):
        if isinstance(node, ast.Name) and node.id not in MOLANG_FUNCTIONS:
            raise ValueError(f"unsupported Molang name {node.id!r}")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise ValueError(f"unsupported Molang call {expression!r}")
    result = float(eval(compile(parsed, "<molang>", "eval"), {"__builtins__": {}}, MOLANG_FUNCTIONS))
    if not math.isfinite(result):
        raise ValueError(f"non-finite Molang result for {expression!r}")
    return result


def keyframe_value(value: Any, time: float) -> Any:
    if not isinstance(value, dict):
        return value
    frames: list[tuple[float, Any]] = []
    for key, frame in value.items():
        try:
            frames.append((float(key), frame))
        except (TypeError, ValueError):
            continue
    if not frames:
        return value.get("post", value.get("pre", value))
    frames.sort(key=lambda item: item[0])
    selected = frames[0][1]
    for frame_time, frame in frames:
        if frame_time <= time:
            selected = frame
        else:
            break
    if isinstance(selected, dict):
        return selected.get("post", selected.get("pre", selected))
    return selected


def sample_channel(
    value: Any,
    channel: str,
    current: list[float],
    time: float,
    context_overrides: dict[str, float] | None = None,
) -> list[float]:
    value = keyframe_value(value, time)
    if isinstance(value, (int, float, str)):
        scalar = molang_value(value, current[0], time, context_overrides)
        return [scalar, scalar, scalar] if channel == "scale" else [scalar, 0.0, 0.0]
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"unsupported {channel} channel {value!r}")
    return [
        molang_value(component, current[index], time, context_overrides)
        for index, component in enumerate(value)
    ]


def canonical_bone(value: str) -> str:
    value = value.split(":")[-1].casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def sample_pose(
    geometry: dict[str, Any],
    animations: list[dict[str, Any]],
    time: float,
    context_overrides: dict[str, float] | None = None,
    position_this_pivot_bones: set[str] | None = None,
) -> tuple[dict[str, dict[str, list[float]]], list[str]]:
    bones = {
        canonical_bone(str(bone.get("name", ""))): bone
        for bone in geometry.get("bones", [])
        if isinstance(bone, dict) and bone.get("name")
    }
    state: dict[str, dict[str, list[float]]] = {}
    warnings: list[str] = []
    for selected in animations:
        animation = selected["animation"]
        animation_bones = animation.get("bones", {})
        if not isinstance(animation_bones, dict):
            continue
        for requested_name, channels in animation_bones.items():
            key = canonical_bone(str(requested_name))
            if key not in bones:
                warnings.append(
                    f"{selected['identifier']}: bone {requested_name!r} is absent from the selected geometry"
                )
                continue
            bone = bones[key]
            if not isinstance(channels, dict):
                continue
            transforms = state.setdefault(key, {
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            })
            for channel in ("position", "rotation", "scale"):
                if channel not in channels:
                    continue
                current_property = transforms[channel]
                if (
                    channel == "position"
                    and position_this_pivot_bones
                    and key in position_this_pivot_bones
                ):
                    base_position = bone.get("pivot", [0.0, 0.0, 0.0])
                    if not isinstance(base_position, list) or len(base_position) != 3:
                        base_position = [0.0, 0.0, 0.0]
                    current_property = [
                        float(base_position[index]) + transforms[channel][index]
                        for index in range(3)
                    ]
                elif channel == "rotation":
                    base_rotation = bone.get("rotation", [0.0, 0.0, 0.0])
                    if not isinstance(base_rotation, list) or len(base_rotation) != 3:
                        base_rotation = [0.0, 0.0, 0.0]
                    current_property = [
                        float(base_rotation[index]) + transforms[channel][index]
                        for index in range(3)
                    ]
                try:
                    sampled = sample_channel(
                        channels[channel], channel, current_property, time, context_overrides
                    )
                except (SyntaxError, TypeError, ValueError, ZeroDivisionError) as exc:
                    warnings.append(
                        f"{selected['identifier']} {requested_name}.{channel}: {exc}"
                    )
                    continue
                if channel == "scale":
                    transforms[channel] = [
                        transforms[channel][index] * sampled[index]
                        for index in range(3)
                    ]
                else:
                    transforms[channel] = [
                        transforms[channel][index] + sampled[index]
                        for index in range(3)
                    ]
    result: dict[str, dict[str, list[float]]] = {}
    for key, transforms in state.items():
        bone = bones[key]
        display_name = str(bone["name"])
        base_rotation = bone.get("rotation", [0.0, 0.0, 0.0])
        if not isinstance(base_rotation, list) or len(base_rotation) != 3:
            base_rotation = [0.0, 0.0, 0.0]
        result[display_name] = {
            "position": transforms["position"],
            "rotation": [float(base_rotation[index]) + transforms["rotation"][index] for index in range(3)],
            "scale": transforms["scale"],
        }
    return result, warnings


def vector_spec(name: str, values: list[float]) -> str:
    formatted = ",".join(f"{value:.8g}" for value in values)
    return f"{name}={formatted}"


def ordered_properties(values: Any, preferred: tuple[str, ...]) -> list[tuple[str, str]]:
    if not isinstance(values, dict):
        return []
    items = [(str(key), str(value)) for key, value in values.items() if isinstance(value, str)]
    priority = {name.casefold(): index for index, name in enumerate(preferred)}
    return [item for _, item in sorted(
        enumerate(items),
        key=lambda item: (priority.get(item[1][0].casefold(), len(priority)), item[0]),
    )]


def choose_geometry(
    description: dict[str, Any],
    index: dict[str, list[dict[str, Any]]],
    entity_name: str,
) -> tuple[dict[str, Any], str] | None:
    for key, requested in ordered_properties(description.get("geometry"), ("default", "adult", "main")):
        candidates = index.get(requested.casefold(), [])
        if not candidates:
            continue
        candidates = sorted(
            candidates,
            key=lambda entry: (
                entity_name.casefold() in Path(entry["path"]).stem.casefold(),
                -len(Path(entry["path"]).as_posix()),
                Path(entry["path"]).as_posix().casefold(),
            ),
            reverse=True,
        )
        return candidates[0], key
    return None


def preferred_geometry_entry(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        candidates,
        key=lambda entry: (
            not bool(entry.get("parent")),
            -len(Path(entry["path"]).as_posix()),
            Path(entry["path"]).as_posix().casefold(),
        ),
        reverse=True,
    )[0]


def flatten_legacy_geometry(
    entry: dict[str, Any],
    index: dict[str, list[dict[str, Any]]],
    stack: tuple[str, ...] = (),
) -> dict[str, Any]:
    identifier = str(entry["identifier"])
    if identifier.casefold() in stack:
        raise ValueError(f"geometry inheritance cycle at {identifier}")
    child = dict(entry["geometry"])
    parent_identifier = entry.get("parent")
    if not parent_identifier:
        return child
    parent_candidates = index.get(str(parent_identifier).casefold(), [])
    if not parent_candidates:
        raise ValueError(f"missing inherited geometry {parent_identifier}")
    parent_entry = preferred_geometry_entry(parent_candidates)
    parent = flatten_legacy_geometry(
        parent_entry,
        index,
        stack + (identifier.casefold(),),
    )
    merged = dict(parent)
    merged.update({key: value for key, value in child.items() if key != "bones"})
    inherited_bones = [dict(bone) for bone in parent.get("bones", []) if isinstance(bone, dict)]
    child_bones = [dict(bone) for bone in child.get("bones", []) if isinstance(bone, dict)]
    positions = {
        str(bone.get("name", "")).casefold(): position
        for position, bone in enumerate(inherited_bones)
        if bone.get("name")
    }
    for bone in child_bones:
        key = str(bone.get("name", "")).casefold()
        if key and key in positions:
            inherited_bones[positions[key]] = bone
        else:
            if key:
                positions[key] = len(inherited_bones)
            inherited_bones.append(bone)
    merged["bones"] = inherited_bones
    return merged


def resolve_texture(resource_pack: Path, reference: str) -> Path | None:
    candidate = resource_pack.joinpath(*reference.replace("\\", "/").split("/"))
    if candidate.is_file():
        return candidate.resolve()
    if candidate.suffix:
        return None
    for extension in IMAGE_EXTENSIONS:
        with_extension = candidate.with_suffix(extension)
        if with_extension.is_file():
            return with_extension.resolve()
    return None


def texture_sort_key(item: tuple[int, tuple[str, str]], entity_name: str) -> tuple[int, int]:
    original_index, (key, _) = item
    folded = key.casefold()
    if folded == "default":
        return 0, original_index
    if folded in {entity_name.casefold(), "base", "main", "normal"}:
        return 1, original_index
    if any(word in folded for word in SECONDARY_TEXTURE_WORDS):
        return 3, original_index
    return 2, original_index


def choose_texture(
    description: dict[str, Any],
    resource_pack: Path,
    entity_name: str,
    preferred_key: str | None = None,
) -> tuple[str, str, Path] | None:
    textures = description.get("textures")
    if not isinstance(textures, dict):
        return None
    values = [(str(key), str(value)) for key, value in textures.items() if isinstance(value, str)]
    if preferred_key:
        for key, reference in values:
            if key.casefold() != preferred_key.casefold():
                continue
            path = resolve_texture(resource_pack, reference)
            if path:
                return key, reference, path
    for _, (key, reference) in sorted(
        enumerate(values), key=lambda item: texture_sort_key(item, entity_name)
    ):
        path = resolve_texture(resource_pack, reference)
        if path:
            return key, reference, path
    return None


def make_visible_pixels_opaque(source: Path, destination: Path) -> None:
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    alpha = image.getchannel("A").point(lambda value: 255 if value else 0)
    image.putalpha(alpha)
    image.save(destination)


def composite_texture(base: Path, overlay: Path, destination: Path) -> None:
    with Image.open(base) as opened:
        base_image = opened.convert("RGBA")
    with Image.open(overlay) as opened:
        overlay_image = opened.convert("RGBA")
    if overlay_image.size != base_image.size:
        raise ValueError(
            f"texture layers have different sizes: {base_image.size} and {overlay_image.size}"
        )
    Image.alpha_composite(base_image, overlay_image).save(destination)


def validate_png(path: Path, expected: str) -> str | None:
    try:
        width, height = (int(value) for value in expected.lower().split("x", 1))
        with Image.open(path) as image:
            if image.size != (width, height):
                return f"expected {width}x{height}, got {image.width}x{image.height}"
            if image.mode != "RGBA":
                return f"expected RGBA, got {image.mode}"
            if image.getchannel("A").getbbox() is None:
                return "image has no visible alpha pixels"
    except (OSError, ValueError) as exc:
        return str(exc)
    return None


def main() -> None:
    args = arguments()
    if args.jobs < 1:
        raise SystemExit("--jobs must be at least 1")
    if args.limit is not None and args.limit < 0:
        raise SystemExit("--limit cannot be negative")

    source = args.source_root.expanduser().resolve()
    resource_pack = source / "resource_pack"
    entity_root = resource_pack / "entity"
    models_root = resource_pack / "models"
    animations_root = resource_pack / "animations"
    for required in (entity_root, models_root, animations_root, resource_pack / "textures"):
        if not required.is_dir():
            raise SystemExit(f"Missing vanilla Bedrock resource folder: {required}")

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = args.report.expanduser().resolve() if args.report else output / "render-report.json"
    renderer = args.renderer.expanduser().resolve() if args.renderer else Path(__file__).with_name("render_model.py")
    if not renderer.is_file():
        raise SystemExit(f"Renderer not found: {renderer}")

    definitions, alternatives, invalid_definitions = collect_definitions(entity_root)
    geometries, invalid_geometries = geometry_index(models_root)
    animations, invalid_animations = animation_index(animations_root)
    requested = {
        value.strip().casefold()
        for value in (args.include or "").split(",")
        if value.strip()
    }
    catalog_excluded = sorted(
        identifier for identifier, (_, description) in definitions.items()
        if args.catalog == "mobs" and "spawn_egg" not in description
    )
    identifiers = sorted(
        identifier for identifier in definitions
        if identifier not in catalog_excluded
        and (
            not requested
            or identifier.casefold() in requested
            or short_identifier(identifier).casefold() in requested
        )
    )
    if args.limit is not None:
        identifiers = identifiers[:args.limit]

    report: dict[str, Any] = {
        "source": str(source),
        "view": args.view,
        "resolution": args.resolution,
        "lighting": args.lighting,
        "background": args.background,
        "catalog": args.catalog,
        "pose": args.pose,
        "poseTime": args.pose_time,
        "definitions": len(list(entity_root.glob("*.entity.json"))),
        "candidates": len(identifiers),
        "catalogExcluded": catalog_excluded,
        "rendered": [],
        "existing": [],
        "unresolved": [],
        "failed": [],
        "invalidDefinitions": invalid_definitions,
        "invalidGeometries": invalid_geometries,
        "invalidAnimations": invalid_animations,
        "entities": {},
    }
    jobs: list[dict[str, Any]] = []
    destinations: dict[str, str] = {}
    assets_directory = tempfile.TemporaryDirectory(prefix="dorios-vanilla-entity-assets-")
    temporary_root = Path(assets_directory.name)
    flattened_root = temporary_root / "models"
    texture_root = temporary_root / "textures"
    flattened_root.mkdir()
    texture_root.mkdir()
    for identifier in identifiers:
        definition_path, description = definitions[identifier]
        entity_name = short_identifier(identifier)
        destination = output / f"{entity_name}.png"
        collision = destinations.get(destination.name.casefold())
        if collision:
            raise SystemExit(f"Duplicate output filename {destination.name}: {collision} and {identifier}")
        destinations[destination.name.casefold()] = identifier

        chosen_geometry = choose_geometry(description, geometries, entity_name)
        chosen_texture = choose_texture(
            description,
            resource_pack,
            entity_name,
            CURATED_TEXTURE_KEYS.get(entity_name),
        )
        entity_report: dict[str, Any] = {
            "definition": definition_path.name,
            "alternativeDefinitions": alternatives.get(identifier, []),
            "geometryKeys": list(description.get("geometry", {})),
            "textureKeys": list(description.get("textures", {})),
        }
        errors: list[str] = []
        if chosen_geometry:
            geometry_entry, geometry_key = chosen_geometry
            geometry_identifier = str(geometry_entry["identifier"])
            geometry_selection = str(geometry_entry["selection"])
            geometry_path = Path(geometry_entry["path"])
            effective_geometry = geometry_entry["geometry"]
            entity_report.update({
                "geometryKey": geometry_key,
                "geometry": geometry_identifier,
                "model": str(geometry_path.relative_to(resource_pack)),
            })
            if geometry_entry.get("parent"):
                try:
                    flattened = flatten_legacy_geometry(geometry_entry, geometries)
                except ValueError as exc:
                    errors.append(str(exc))
                else:
                    entity_report["inheritedGeometry"] = str(geometry_entry["parent"])
                    effective_geometry = flattened
                    geometry_path = flattened_root / f"{entity_name}.json"
                    geometry_path.write_text(
                        json.dumps({"format_version": "1.8.0", geometry_identifier: flattened}),
                        encoding="utf-8",
                    )
                    geometry_selection = geometry_identifier
            bind_rotations = (
                CURATED_BIND_POSE_ROTATIONS.get(entity_name, {})
                if args.pose == "curated"
                else {}
            )
            if bind_rotations:
                effective_geometry = deepcopy(effective_geometry)
                applied_bind_rotations: dict[str, list[float]] = {}
                for bone in effective_geometry.get("bones", []):
                    if not isinstance(bone, dict):
                        continue
                    requested_rotation = bind_rotations.get(
                        str(bone.get("name", "")).casefold()
                    )
                    if requested_rotation is None:
                        continue
                    bone["bind_pose_rotation"] = list(requested_rotation)
                    applied_bind_rotations[str(bone.get("name"))] = list(requested_rotation)
                if applied_bind_rotations:
                    geometry_path = flattened_root / f"{entity_name}_curated.json"
                    if geometry_entry.get("legacy"):
                        geometry_document = {
                            "format_version": "1.8.0",
                            geometry_identifier: effective_geometry,
                        }
                    else:
                        geometry_document = {
                            "format_version": "1.21.0",
                            "minecraft:geometry": [effective_geometry],
                        }
                    geometry_path.write_text(
                        json.dumps(geometry_document),
                        encoding="utf-8",
                    )
                    geometry_selection = geometry_identifier
                    entity_report["curatedBindPoseRotations"] = applied_bind_rotations
        else:
            errors.append("no referenced geometry identifier was found below resource_pack/models")
        if chosen_texture:
            texture_key, texture_reference, texture_path = chosen_texture
            entity_report.update({
                "textureKey": texture_key,
                "texture": texture_reference,
                "textureFile": str(texture_path.relative_to(resource_pack)),
            })
            render_texture_path = texture_path
            if entity_name == "trader_llama":
                overlay_reference = description.get("textures", {}).get("decor_wandering_trader")
                overlay_path = (
                    resolve_texture(resource_pack, overlay_reference)
                    if isinstance(overlay_reference, str)
                    else None
                )
                if not overlay_path:
                    errors.append("wandering trader llama decor texture was not found")
                else:
                    render_texture_path = texture_root / "trader_llama_composite.png"
                    try:
                        composite_texture(texture_path, overlay_path, render_texture_path)
                    except (OSError, ValueError) as exc:
                        errors.append(f"could not composite trader llama decor: {exc}")
                    else:
                        entity_report["textureLayers"] = [
                            texture_reference,
                            overlay_reference,
                        ]
            if entity_name in OPAQUE_VISIBLE_TEXTURES and not errors:
                opaque_path = texture_root / f"{entity_name}_opaque.png"
                try:
                    make_visible_pixels_opaque(render_texture_path, opaque_path)
                except OSError as exc:
                    errors.append(f"could not make visible texture pixels opaque: {exc}")
                else:
                    render_texture_path = opaque_path
                    entity_report["alphaMode"] = "opaque-visible-pixels"
        else:
            errors.append("no referenced texture file was found below resource_pack")
        pose_transforms: dict[str, dict[str, list[float]]] = {}
        selected_animations: list[dict[str, Any]] = []
        pose_selection_warnings: list[str] = []
        if args.pose == "curated":
            selected_animations, pose_selection_warnings = choose_curated_pose_animations(
                entity_name, animations
            )
        elif args.pose == "auto":
            selected_animations = choose_pose_animations(description, animations)
        if args.pose != "bind" and chosen_geometry and not errors:
            entity_report["poseAnimations"] = [
                {
                    "alias": selected["alias"],
                    "identifier": selected["identifier"],
                    "file": str(Path(selected["path"]).relative_to(resource_pack)),
                }
                for selected in selected_animations
            ]
            if selected_animations:
                pose_transforms, pose_warnings = sample_pose(
                    effective_geometry,
                    selected_animations,
                    args.pose_time,
                    CURATED_MOLANG_CONTEXT.get(entity_name) if args.pose == "curated" else None,
                    CURATED_POSITION_THIS_PIVOT_BONES.get(entity_name)
                    if args.pose == "curated"
                    else None,
                )
                entity_report["poseTransforms"] = pose_transforms
                pose_selection_warnings.extend(pose_warnings)
            if pose_selection_warnings:
                entity_report["poseWarnings"] = pose_selection_warnings
        hidden_bones = (
            list(CURATED_HIDDEN_BONES.get(entity_name, ()))
            if args.pose == "curated"
            else []
        )
        model_rotation = (
            CURATED_MODEL_ROTATIONS.get(entity_name, "0,0,0")
            if args.pose == "curated"
            else "0,0,0"
        )
        if hidden_bones:
            entity_report["hiddenBones"] = hidden_bones
        if model_rotation != "0,0,0":
            entity_report["modelRotation"] = model_rotation
        layered_manifest: Path | None = None
        if args.pose == "curated" and entity_name == "breeze" and not errors:
            geometry_mappings = description.get("geometry", {})
            texture_mappings = description.get("textures", {})
            layer_blocks: list[dict[str, Any]] = []
            layer_report: list[dict[str, str]] = []
            for layer_name, geometry_key, texture_key in BREEZE_RENDER_LAYERS:
                requested_geometry = (
                    geometry_mappings.get(geometry_key)
                    if isinstance(geometry_mappings, dict)
                    else None
                )
                requested_texture = (
                    texture_mappings.get(texture_key)
                    if isinstance(texture_mappings, dict)
                    else None
                )
                candidates = (
                    geometries.get(str(requested_geometry).casefold(), [])
                    if isinstance(requested_geometry, str)
                    else []
                )
                layer_texture = (
                    resolve_texture(resource_pack, requested_texture)
                    if isinstance(requested_texture, str)
                    else None
                )
                if not candidates or not layer_texture:
                    errors.append(
                        f"missing Breeze layer {layer_name}: "
                        f"geometry={requested_geometry!r} texture={requested_texture!r}"
                    )
                    continue
                layer_geometry = sorted(
                    candidates,
                    key=lambda entry: (
                        entity_name.casefold() in Path(entry["path"]).stem.casefold(),
                        -len(Path(entry["path"]).as_posix()),
                        Path(entry["path"]).as_posix().casefold(),
                    ),
                    reverse=True,
                )[0]
                layer_model_path = Path(layer_geometry["path"]).resolve()
                layer_selection = str(layer_geometry["selection"])
                if layer_name == "eyes":
                    eyes_geometry = deepcopy(layer_geometry["geometry"])
                    for bone in eyes_geometry.get("bones", []):
                        if (
                            isinstance(bone, dict)
                            and str(bone.get("name", "")).casefold() == "head"
                        ):
                            bone.pop("cubes", None)
                    layer_model_path = temporary_root / "breeze_eyes_only.json"
                    layer_model_path.write_text(
                        json.dumps({
                            "format_version": "1.12.0",
                            "minecraft:geometry": [eyes_geometry],
                        }),
                        encoding="utf-8",
                    )
                    layer_selection = str(layer_geometry["identifier"])
                positions = [
                    vector_spec(bone_name, transforms["position"])
                    for bone_name, transforms in pose_transforms.items()
                    if any(abs(value) > 1e-8 for value in transforms["position"])
                ]
                block: dict[str, Any] = {
                    "name": layer_name,
                    "model": str(layer_model_path),
                    "geometry": layer_selection,
                    "textures": [str(layer_texture)],
                    "preserve_origin": True,
                }
                if positions:
                    block["bone_positions"] = positions
                layer_blocks.append(block)
                layer_report.append({
                    "name": layer_name,
                    "geometry": str(layer_geometry["identifier"]),
                    "texture": str(requested_texture),
                })
            if not errors:
                layered_manifest = temporary_root / "breeze_layers.json"
                layered_manifest.write_text(
                    json.dumps({"blocks": layer_blocks}),
                    encoding="utf-8",
                )
                entity_report["renderLayers"] = layer_report
        report["entities"][identifier] = entity_report
        if errors:
            report["unresolved"].append({"id": identifier, "errors": errors})
            continue
        if args.skip_existing and destination.is_file() and validate_png(destination, args.resolution) is None:
            report["existing"].append(identifier)
            continue
        if args.classify_only:
            continue

        source_arguments = (
            ["--manifest", str(layered_manifest)]
            if layered_manifest
            else [
                "--model", str(geometry_path),
                "--textures", str(render_texture_path),
                "--geometry", geometry_selection,
            ]
        )
        command = [
            sys.executable, str(renderer),
            *source_arguments,
            "--output", str(destination),
            "--view", args.view,
            "--resolution", args.resolution,
            "--background", args.background,
            "--lighting", args.lighting,
            "--ground", "off",
            "--texture-filter", "closest",
            "--model-rotation", model_rotation,
        ]
        if args.render_resolution:
            command.extend(["--render-resolution", args.render_resolution])
        if args.blender:
            command.extend(["--blender", args.blender])
        if not layered_manifest:
            for bone_name in hidden_bones:
                command.extend(["--hide-bone", bone_name])
            for bone_name, transforms in pose_transforms.items():
                position = transforms["position"]
                rotation = transforms["rotation"]
                scale = transforms["scale"]
                if any(abs(value) > 1e-8 for value in position):
                    command.extend(["--bone-position", vector_spec(bone_name, position)])
                if any(abs(value) > 1e-8 for value in rotation):
                    command.extend(["--bone-rotation", vector_spec(bone_name, rotation)])
                if any(abs(value - 1.0) > 1e-8 for value in scale):
                    command.extend(["--bone-scale", vector_spec(bone_name, scale)])
        jobs.append({"id": identifier, "output": destination, "command": command})

    def render(job: dict[str, Any]) -> tuple[dict[str, Any], subprocess.CompletedProcess[str], str | None]:
        completed = subprocess.run(job["command"], capture_output=True, text=True, check=False)
        validation = validate_png(job["output"], args.resolution) if completed.returncode == 0 else None
        return job, completed, validation

    if not args.classify_only:
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = [executor.submit(render, job) for job in jobs]
            for completed_count, future in enumerate(as_completed(futures), 1):
                job, completed, validation = future.result()
                identifier = job["id"]
                if completed.returncode == 0 and validation is None:
                    report["rendered"].append(identifier)
                else:
                    report["failed"].append({
                        "id": identifier,
                        "validation": validation,
                        "stdout": completed.stdout[-3000:],
                        "stderr": completed.stderr[-3000:],
                    })
                if completed_count % 10 == 0 or completed_count == len(jobs):
                    print(
                        f"[{completed_count}/{len(jobs)}] rendered={len(report['rendered'])} "
                        f"failed={len(report['failed'])}",
                        flush=True,
                    )
    assets_directory.cleanup()

    report["rendered"] = sorted(report["rendered"])
    report["existing"] = sorted(report["existing"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"Candidates={report['candidates']} rendered={len(report['rendered'])} "
        f"existing={len(report['existing'])} unresolved={len(report['unresolved'])} "
        f"failed={len(report['failed'])}"
    )
    print(f"Report: {report_path}")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
