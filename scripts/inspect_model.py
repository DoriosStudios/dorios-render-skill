#!/usr/bin/env python3
"""Inspect supported model files without requiring Blender."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MESH_EXTENSIONS = {".blend", ".fbx", ".glb", ".gltf", ".obj"}


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def bedrock_geometries(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("minecraft:geometry")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    legacy = []
    for key, item in data.items():
        if key.startswith("geometry.") and isinstance(item, dict):
            copied = dict(item)
            copied.setdefault("description", {})
            copied["description"].setdefault("identifier", key)
            legacy.append(copied)
    if legacy:
        return legacy
    if isinstance(data.get("bones"), list):
        return [data]
    return []


def detect_format(path: Path, data: dict[str, Any] | None = None) -> str:
    if path.suffix.lower() in MESH_EXTENSIONS:
        return path.suffix.lower().lstrip(".").upper()
    if data is None:
        return "unknown"
    if isinstance(data.get("minecraft:block"), dict):
        return "Minecraft Bedrock block definition"
    if bedrock_geometries(data):
        return "Minecraft Bedrock geometry"
    meta = data.get("meta")
    if path.suffix.lower() == ".bbmodel" or (
        isinstance(meta, dict) and "model_format" in meta and "elements" in data
    ):
        return "Blockbench BBMODEL"
    if any(key in data for key in ("parent", "textures", "elements")):
        return "Minecraft Java block model"
    return "unknown JSON"


def print_bedrock(data: dict[str, Any]) -> None:
    geometries = bedrock_geometries(data)
    print(f"Geometries: {len(geometries)}")
    for index, geometry in enumerate(geometries):
        desc = geometry.get("description", {})
        identifier = desc.get("identifier", f"geometry[{index}]")
        bones = geometry.get("bones", [])
        cubes = sum(len(b.get("cubes", [])) for b in bones if isinstance(b, dict))
        print(f"  - {identifier}: {len(bones)} bones, {cubes} cubes")
        for bone in bones:
            if not isinstance(bone, dict):
                continue
            parent = f" -> parent: {bone['parent']}" if bone.get("parent") else ""
            count = len(bone.get("cubes", []))
            print(f"      {bone.get('name', '<unnamed>')} ({count} cubes){parent}")
        width = desc.get("texture_width", geometry.get("texturewidth"))
        height = desc.get("texture_height", geometry.get("textureheight"))
        if width and height:
            print(f"    Texture atlas: {width}x{height}")


def walk_outliner(nodes: list[Any], depth: int = 0) -> None:
    for node in nodes:
        if isinstance(node, str):
            print(f"{'  ' * depth}- element {node}")
            continue
        if not isinstance(node, dict):
            continue
        print(f"{'  ' * depth}- group {node.get('name', '<unnamed>')}")
        children = node.get("children", [])
        if isinstance(children, list):
            walk_outliner(children, depth + 1)


def inspect(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"Model not found: {path}")
    if path.suffix.lower() in MESH_EXTENSIONS:
        print(f"Format: {detect_format(path)}")
        print("Bones/materials require Blender inspection for this binary/mesh format.")
        return
    data = load_json(path)
    model_format = detect_format(path, data)
    print(f"Format: {model_format}")
    if model_format == "Minecraft Bedrock geometry":
        print_bedrock(data)
    elif model_format == "Minecraft Bedrock block definition":
        block = data["minecraft:block"]
        description = block.get("description", {})
        components = block.get("components", {})
        print(f"Identifier: {description.get('identifier', '<unnamed>')}")
        print(f"Geometry: {components.get('minecraft:geometry', '<default full block>')}")
        materials = components.get("minecraft:material_instances", {})
        print("Material instances:")
        for face, material in materials.items() if isinstance(materials, dict) else []:
            texture = material.get("texture") if isinstance(material, dict) else material
            print(f"  - {face}: {texture}")
    elif model_format == "Blockbench BBMODEL":
        elements = data.get("elements", [])
        cubes = [item for item in elements if isinstance(item, dict) and item.get("type", "cube") == "cube"]
        meshes = [item for item in elements if isinstance(item, dict) and item.get("type") == "mesh"]
        print(f"Elements: {len(cubes)} cubes, {len(meshes)} meshes")
        print("Outliner:")
        walk_outliner(data.get("outliner", []))
        textures = data.get("textures", [])
        print(f"Texture entries: {len(textures) if isinstance(textures, list) else 0}")
    elif model_format == "Minecraft Java block model":
        print(f"Parent: {data.get('parent', '<none>')}")
        textures = data.get("textures", {})
        print("Texture variables:")
        for key, value in textures.items() if isinstance(textures, dict) else []:
            print(f"  - #{key} = {value}")
        elements = data.get("elements", [])
        print(f"Elements: {len(elements) if isinstance(elements, list) else 0}")
    else:
        print(f"Top-level keys: {', '.join(sorted(data.keys()))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    args = parser.parse_args()
    inspect(args.model.resolve())


if __name__ == "__main__":
    main()
