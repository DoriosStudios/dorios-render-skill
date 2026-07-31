#!/usr/bin/env python3
"""Blender-side worker for Dorios Render Skill. Run through render_model.py."""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))
from json_utils import load_jsonc


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp"}
FACE_NAMES = ("north", "south", "west", "east", "down", "up")


def arguments() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--model", type=Path)
    source.add_argument("--manifest", type=Path)
    parser.add_argument("--textures", nargs="*", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--view", default="iso-ne")
    parser.add_argument("--azimuth", type=float, default=45.0)
    parser.add_argument("--elevation", type=float, default=30.0)
    parser.add_argument("--model-rotation", default="0,0,0")
    parser.add_argument("--hide-bone", action="append", default=[])
    parser.add_argument("--bone-rotation", action="append", default=[])
    parser.add_argument("--geometry")
    parser.add_argument("--resource-pack", type=Path)
    parser.add_argument("--ortho-scale", type=float)
    parser.add_argument("--resolution", default="1024x1024")
    parser.add_argument("--margin", type=float, default=0.025)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--background", default="transparent")
    parser.add_argument(
        "--lighting",
        choices=["balanced", "left_light", "right_light", "studio", "flat", "dramatic"],
        default="right_light",
    )
    parser.add_argument("--ground", default="auto")
    parser.add_argument("--no-shadows", action="store_true")
    parser.add_argument("--texture-filter", default="closest")
    return parser.parse_args(argv)


def triple(value: str, label: str) -> tuple[float, float, float]:
    try:
        parts = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise RuntimeError(f"Invalid {label}: {value}; expected x,y,z") from exc
    if len(parts) != 3:
        raise RuntimeError(f"Invalid {label}: {value}; expected x,y,z")
    return parts


def model_to_blender(point: list[float] | tuple[float, ...]) -> Vector:
    return Vector((float(point[0]), -float(point[2]), float(point[1])))


def model_rotation(value: list[float] | tuple[float, ...]) -> tuple[float, float, float]:
    return tuple(math.radians(item) for item in (float(value[0]), -float(value[2]), float(value[1])))


def canonical(value: str) -> str:
    value = value.split(":")[-1].lower()
    return re.sub(r"[^a-z0-9]+", "", value)


def load_json(path: Path) -> dict[str, Any]:
    return load_jsonc(path)


def bedrock_geometries(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("minecraft:geometry")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    result = []
    for key, item in data.items():
        if key.startswith("geometry.") and isinstance(item, dict):
            copied = dict(item)
            copied.setdefault("description", {})
            copied["description"].setdefault("identifier", key)
            result.append(copied)
    if result:
        return result
    return [data] if isinstance(data.get("bones"), list) else []


class TextureCatalog:
    def __init__(self, paths: list[Path], interpolation: str, emission: float = 0.16) -> None:
        self.files: list[Path] = []
        self.interpolation = "Closest" if interpolation == "closest" else "Linear"
        self.emission = emission
        for supplied in paths:
            if supplied.is_dir():
                self.files.extend(
                    path.resolve() for path in supplied.rglob("*") if path.suffix.lower() in IMAGE_EXTENSIONS
                )
            elif supplied.suffix.lower() in IMAGE_EXTENSIONS:
                self.files.append(supplied.resolve())
        self.files = sorted(set(self.files))
        self.materials: dict[Path | None, bpy.types.Material] = {}

    @staticmethod
    def key(value: str) -> str:
        clean = value.replace("\\", "/").split(":")[-1]
        clean = re.sub(r"\.(png|jpg|jpeg|tga|bmp|webp)$", "", clean, flags=re.I)
        clean = re.sub(r"^(textures?/)?(blocks?|entity|items?)/", "", clean, flags=re.I)
        return canonical(clean)

    def resolve(self, reference: str | None = None) -> Path | None:
        if not self.files:
            return None
        if reference:
            wanted = self.key(reference)
            exact = [path for path in self.files if self.key(path.stem) == wanted]
            if len(exact) == 1:
                return exact[0]
            suffix = [path for path in self.files if self.key(path.as_posix()).endswith(wanted)]
            if len(suffix) == 1:
                return suffix[0]
        return self.files[0] if len(self.files) == 1 else None

    def material(self, reference: str | None = None) -> bpy.types.Material:
        image_path = self.resolve(reference)
        if image_path in self.materials:
            return self.materials[image_path]
        material = bpy.data.materials.new("Texture:" + (image_path.stem if image_path else "missing"))
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        shader.inputs["Roughness"].default_value = 0.82
        if image_path:
            image = bpy.data.images.load(str(image_path), check_existing=True)
            texture = nodes.new("ShaderNodeTexImage")
            texture.image = image
            texture.interpolation = self.interpolation
            links.new(texture.outputs["Color"], shader.inputs["Base Color"])
            links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
            emission_input = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
            emission_strength = shader.inputs.get("Emission Strength")
            if emission_input:
                links.new(texture.outputs["Color"], emission_input)
            if emission_strength:
                emission_strength.default_value = self.emission
            try:
                material.surface_render_method = "DITHERED"
            except AttributeError:
                material.blend_method = "BLEND"
                material.use_screen_refraction = True
        else:
            shader.inputs["Base Color"].default_value = (0.8, 0.12, 0.8, 1.0)
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
        self.materials[image_path] = material
        return material

    def first_image_size(self, reference: str | None = None) -> tuple[int, int] | None:
        image_path = self.resolve(reference)
        if not image_path:
            return None
        image = bpy.data.images.load(str(image_path), check_existing=True)
        return int(image.size[0]), int(image.size[1])


def uv_quad(
    rect: list[float] | tuple[float, ...],
    width: float,
    height: float,
    mirror: bool = False,
    rotation: int = 0,
) -> list[tuple[float, float]]:
    u1, v1, u2, v2 = map(float, rect)
    if mirror:
        u1, u2 = u2, u1
    coords = [(u1 / width, 1 - v2 / height), (u2 / width, 1 - v2 / height),
              (u2 / width, 1 - v1 / height), (u1 / width, 1 - v1 / height)]
    quarter_turns = (int(rotation) // 90) % 4
    return coords[quarter_turns:] + coords[:quarter_turns]


def default_box_uv(offset: list[float], size: list[float]) -> dict[str, list[float]]:
    u, v = map(float, offset)
    x, y, z = map(float, size)
    return {
        "west": [u, v + z, u + z, v + z + y],
        "north": [u + z, v + z, u + z + x, v + z + y],
        "east": [u + z + x, v + z, u + 2 * z + x, v + z + y],
        "south": [u + 2 * z + x, v + z, u + 2 * (z + x), v + z + y],
        "up": [u + z, v, u + z + x, v + z],
        "down": [u + z + x, v, u + z + 2 * x, v + z],
    }


def cube_vertices(bounds: tuple[list[float], list[float]]) -> dict[str, list[Vector]]:
    lower, upper = bounds
    x0, y0, z0 = lower
    x1, y1, z1 = upper
    model_faces = {
        "north": [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)],
        "south": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        "west": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
        "east": [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)],
        "down": [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)],
        "up": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
    }
    return {name: [model_to_blender(vertex) for vertex in vertices] for name, vertices in model_faces.items()}


def create_cube(
    name: str,
    lower: list[float],
    upper: list[float],
    face_data: dict[str, dict[str, Any]],
    catalog: TextureCatalog,
    texture_size: tuple[float, float] = (16, 16),
    mirror: bool = False,
) -> bpy.types.Object:
    vertices: list[Vector] = []
    faces: list[tuple[int, int, int, int]] = []
    specs: list[tuple[str, dict[str, Any]]] = []
    for face_name, corners in cube_vertices((lower, upper)).items():
        spec = face_data.get(face_name)
        if spec is None:
            continue
        start = len(vertices)
        vertices.extend(corners)
        faces.append((start, start + 1, start + 2, start + 3))
        specs.append((face_name, spec))
    mesh = bpy.data.meshes.new(name + ":mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    material_slots: dict[str, int] = {}
    for polygon, (_, spec) in zip(mesh.polygons, specs):
        reference = spec.get("texture")
        slot_key = str(reference)
        if slot_key not in material_slots:
            obj.data.materials.append(catalog.material(reference))
            material_slots[slot_key] = len(obj.data.materials) - 1
        polygon.material_index = material_slots[slot_key]
        face_texture_size = tuple(spec.get("texture_size", texture_size))
        rect = spec.get("uv", [0, 0, face_texture_size[0], face_texture_size[1]])
        if len(rect) == 2:
            uv_size = spec.get("uv_size", [texture_size[0], texture_size[1]])
            rect = [rect[0], rect[1], rect[0] + uv_size[0], rect[1] + uv_size[1]]
        uv_scale = spec.get("uv_scale", [1, 1])
        rect = [
            float(rect[0]) * float(uv_scale[0]),
            float(rect[1]) * float(uv_scale[1]),
            float(rect[2]) * float(uv_scale[0]),
            float(rect[3]) * float(uv_scale[1]),
        ]
        coords = uv_quad(
            rect,
            face_texture_size[0],
            face_texture_size[1],
            mirror,
            int(spec.get("uv_rotation", 0)),
        )
        for loop_index, uv in zip(polygon.loop_indices, coords):
            uv_layer.data[loop_index].uv = uv
    return obj


def parent_keep_world(child: bpy.types.Object, parent: bpy.types.Object) -> None:
    world_matrix = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = world_matrix


def bedrock_faces(cube: dict[str, Any], size: list[float]) -> dict[str, dict[str, Any]]:
    value = cube.get("uv", [0, 0])
    if isinstance(value, list):
        return {name: {"uv": rect} for name, rect in default_box_uv(value, size).items()}
    if isinstance(value, dict):
        result = {}
        for name in FACE_NAMES:
            entry = value.get(name)
            if isinstance(entry, list):
                result[name] = {"uv": entry}
            elif isinstance(entry, dict) and "uv" in entry:
                result[name] = {
                    "uv": entry["uv"],
                    "uv_size": entry.get("uv_size", size[:2]),
                    "uv_rotation": entry.get("uv_rotation", 0),
                    "material_instance": entry.get("material_instance"),
                }
        return result
    return {name: {"uv": [0, 0, 16, 16]} for name in FACE_NAMES}


def import_bedrock(
    data: dict[str, Any],
    args: argparse.Namespace,
    catalog: TextureCatalog,
    atlas_reference_override: str | None = None,
    material_references: dict[str, str] | None = None,
) -> list[bpy.types.Object]:
    geometries = bedrock_geometries(data)
    if args.geometry:
        matches = [g for g in geometries if g.get("description", {}).get("identifier") == args.geometry]
        if not matches:
            available = [g.get("description", {}).get("identifier", "<unnamed>") for g in geometries]
            raise RuntimeError(f"Geometry {args.geometry!r} not found. Available: {available}")
        geometry = matches[0]
    elif len(geometries) == 1:
        geometry = geometries[0]
    else:
        available = [g.get("description", {}).get("identifier", "<unnamed>") for g in geometries]
        raise RuntimeError(f"Multiple geometries found; pass --geometry. Available: {available}")
    desc = geometry.get("description", {})
    identifier = str(desc.get("identifier", ""))
    atlas_reference = atlas_reference_override or identifier.removeprefix("geometry.") or args.model.stem.replace(".geo", "")
    atlas_path = catalog.resolve(atlas_reference) or catalog.resolve()
    if catalog.files and atlas_path is None:
        names = [path.name for path in catalog.files]
        raise RuntimeError(
            f"Could not choose one Bedrock atlas for {identifier or args.model.name}. "
            f"Pass the texture file directly. Candidates: {names}"
        )
    image_size = catalog.first_image_size(atlas_reference) or catalog.first_image_size() or (64, 64)
    texture_size = (
        float(desc.get("texture_width", geometry.get("texturewidth", image_size[0]))),
        float(desc.get("texture_height", geometry.get("textureheight", image_size[1]))),
    )
    bones = [bone for bone in geometry.get("bones", []) if isinstance(bone, dict)]
    nodes: dict[str, bpy.types.Object] = {}
    base_rotations: dict[str, list[float]] = {}
    for bone in bones:
        name = str(bone.get("name", f"bone_{len(nodes)}"))
        node = bpy.data.objects.new("Bone:" + name, None)
        node.empty_display_type = "PLAIN_AXES"
        node.location = model_to_blender(bone.get("pivot", [0, 0, 0]))
        bpy.context.collection.objects.link(node)
        nodes[canonical(name)] = node
        base_rotations[canonical(name)] = bone.get("rotation", [0, 0, 0])
    for bone in bones:
        key = canonical(str(bone.get("name", "")))
        parent_name = bone.get("parent")
        if parent_name and canonical(str(parent_name)) in nodes:
            parent_keep_world(nodes[key], nodes[canonical(str(parent_name))])
    created: list[bpy.types.Object] = []
    for bone in bones:
        key = canonical(str(bone.get("name", "")))
        bone_node = nodes[key]
        for index, cube in enumerate(bone.get("cubes", [])):
            if not isinstance(cube, dict):
                continue
            origin = [float(item) for item in cube.get("origin", [0, 0, 0])]
            size = [float(item) for item in cube.get("size", [0, 0, 0])]
            inflate = float(cube.get("inflate", 0.0))
            lower = [origin[i] - inflate for i in range(3)]
            upper = [origin[i] + size[i] + inflate for i in range(3)]
            faces = bedrock_faces(cube, size)
            for face_name, face in faces.items():
                instance_name = face.get("material_instance")
                if not instance_name and material_references and face_name in material_references:
                    instance_name = face_name
                reference = None
                if material_references:
                    reference = material_references.get(str(instance_name)) if instance_name else None
                    reference = reference or material_references.get("*")
                reference = reference or (str(atlas_path) if atlas_path else atlas_reference)
                face["texture"] = reference
                actual_size = catalog.first_image_size(reference)
                if actual_size:
                    face["texture_size"] = actual_size
                    if tuple(map(float, actual_size)) != tuple(map(float, texture_size)):
                        face["uv_scale"] = [
                            float(actual_size[0]) / texture_size[0],
                            float(actual_size[1]) / texture_size[1],
                        ]
            obj = create_cube(
                f"{bone_node.name}:cube_{index}", lower, upper, faces,
                catalog, texture_size, bool(cube.get("mirror", bone.get("mirror", False))),
            )
            rotation = cube.get("rotation", [0, 0, 0])
            if any(float(value) for value in rotation):
                pivot = bpy.data.objects.new(obj.name + ":pivot", None)
                pivot.location = model_to_blender(cube.get("pivot", bone.get("pivot", [0, 0, 0])))
                bpy.context.collection.objects.link(pivot)
                parent_keep_world(pivot, bone_node)
                parent_keep_world(obj, pivot)
                pivot.rotation_euler = model_rotation(rotation)
            else:
                parent_keep_world(obj, bone_node)
            created.append(obj)
    for key, rotation in base_rotations.items():
        nodes[key].rotation_euler = model_rotation(rotation)
    return [*nodes.values(), *created]


def resource_pack_path(args: argparse.Namespace) -> Path | None:
    if args.resource_pack:
        return args.resource_pack.resolve()
    for parent in args.model.parents:
        if parent.name.lower() == "bp":
            candidate = parent.with_name("RP")
            if candidate.is_dir():
                return candidate.resolve()
    return None


def terrain_references(resource_pack: Path | None) -> dict[str, str]:
    if resource_pack is None:
        return {}
    path = resource_pack / "textures" / "terrain_texture.json"
    if not path.is_file():
        return {}
    data = load_json(path).get("texture_data", {})
    result: dict[str, str] = {}
    for key, entry in data.items() if isinstance(data, dict) else []:
        value = entry.get("textures") if isinstance(entry, dict) else entry
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, str):
            result[str(key)] = value
    return result


def find_geometry(resource_pack: Path, identifier: str) -> dict[str, Any]:
    model_roots = [resource_pack / "models"]
    workspace = resource_pack.parent.parent
    if workspace.is_dir():
        model_roots.extend(
            candidate for candidate in workspace.glob("*/RP/models")
            if candidate.is_dir() and candidate not in model_roots
        )
    for model_root in model_roots:
        for path in model_root.rglob("*.json") if model_root.is_dir() else []:
            try:
                data = load_json(path)
            except (OSError, ValueError):
                continue
            if any(
                geometry.get("description", {}).get("identifier") == identifier
                for geometry in bedrock_geometries(data)
            ):
                return data
    searched = ", ".join(map(str, model_roots))
    raise RuntimeError(f"Bedrock geometry {identifier!r} was not found below: {searched}")


def legacy_block_materials(resource_pack: Path | None, identifier: str) -> dict[str, Any]:
    if resource_pack is None:
        return {}
    path = resource_pack / "blocks.json"
    if not path.is_file():
        return {}
    entry = load_json(path).get(identifier, {})
    textures = entry.get("textures") if isinstance(entry, dict) else None
    if isinstance(textures, str):
        return {"*": {"texture": textures}}
    if isinstance(textures, dict):
        result: dict[str, Any] = {}
        side = textures.get("side") or textures.get("*")
        if side:
            result["*"] = {"texture": side}
        for face in FACE_NAMES:
            value = textures.get(face)
            if value:
                result[face] = {"texture": value}
        return result
    return {}


def import_bedrock_block(data: dict[str, Any], args: argparse.Namespace, catalog: TextureCatalog) -> list[bpy.types.Object]:
    block = data.get("minecraft:block", {})
    components = block.get("components", {})
    components = dict(components) if isinstance(components, dict) else {}
    identifier = str(block.get("description", {}).get("identifier", args.model.stem))
    materials = components.get("minecraft:material_instances", {})
    if not isinstance(materials, dict) or not materials:
        # Catalog renders need one representative state. When a block only
        # supplies materials through permutations (for example staged crops),
        # prefer the last material-bearing permutation, which is conventionally
        # the mature/complete state, and overlay it on the base components.
        permutations = block.get("permutations", [])
        for permutation in reversed(permutations if isinstance(permutations, list) else []):
            permutation_components = permutation.get("components", {}) if isinstance(permutation, dict) else {}
            permutation_materials = (
                permutation_components.get("minecraft:material_instances", {})
                if isinstance(permutation_components, dict)
                else {}
            )
            if isinstance(permutation_materials, dict) and permutation_materials:
                components.update(permutation_components)
                materials = permutation_materials
                break
    resource_pack = resource_pack_path(args)
    if not isinstance(materials, dict) or not materials:
        materials = legacy_block_materials(resource_pack, identifier)
    if not materials:
        raise RuntimeError(
            f"Bedrock block {identifier} has neither minecraft:material_instances nor legacy RP/blocks.json textures"
        )
    terrain = terrain_references(resource_pack)
    default_material = materials.get("*", {})

    def texture_for(face_name: str) -> str:
        entry = materials.get(face_name, default_material)
        key = entry.get("texture") if isinstance(entry, dict) else entry
        if not isinstance(key, str):
            raise RuntimeError(f"Bedrock block {identifier} has no texture for face {face_name}")
        return terrain.get(key, key)

    material_references: dict[str, str] = {}
    for material_name, entry in materials.items():
        key = entry.get("texture") if isinstance(entry, dict) else entry
        if isinstance(key, str):
            material_references[str(material_name)] = terrain.get(key, key)

    geometry = components.get("minecraft:geometry", "minecraft:geometry.full_block")
    geometry_identifier = geometry.get("identifier") if isinstance(geometry, dict) else geometry
    if geometry_identifier in ("minecraft:geometry.full_block", "geometry.full_block", None):
        faces = {
            name: {"texture": texture_for(name), "uv": [0, 0, 16, 16]}
            for name in FACE_NAMES
        }
        return [create_cube("Block:" + identifier, [0, 0, 0], [16, 16, 16], faces, catalog, (16, 16))]
    if resource_pack is None:
        raise RuntimeError(
            f"Block {identifier} uses custom geometry {geometry_identifier}; pass --resource-pack"
        )
    geometry_data = find_geometry(resource_pack, str(geometry_identifier))
    previous_geometry = args.geometry
    args.geometry = str(geometry_identifier)
    try:
        return import_bedrock(
            geometry_data,
            args,
            catalog,
            texture_for("north"),
            material_references,
        )
    finally:
        args.geometry = previous_geometry


def resolve_java_texture(textures: dict[str, Any], reference: str) -> str:
    seen: set[str] = set()
    value = reference
    while isinstance(value, str) and value.startswith("#"):
        key = value[1:]
        if key in seen:
            break
        seen.add(key)
        value = textures.get(key, value)
    return str(value)


def synthetic_java_faces(parent: str) -> dict[str, dict[str, Any]]:
    if "cube_column" in parent:
        return {name: {"texture": "#end" if name in ("up", "down") else "#side", "uv": [0, 0, 16, 16]} for name in FACE_NAMES}
    variable = "#all" if "cube_all" in parent else "#texture"
    return {name: {"texture": variable, "uv": [0, 0, 16, 16]} for name in FACE_NAMES}


def import_java(data: dict[str, Any], catalog: TextureCatalog) -> list[bpy.types.Object]:
    textures = data.get("textures", {}) if isinstance(data.get("textures"), dict) else {}
    elements = data.get("elements", [])
    if not elements:
        parent = str(data.get("parent", ""))
        if any(name in parent for name in ("cube_all", "cube_column", "cube")):
            elements = [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": synthetic_java_faces(parent)}]
        else:
            raise RuntimeError("Java model has no explicit elements and its parent cannot be synthesized")
    created = []
    for index, element in enumerate(elements):
        faces = {}
        for name, face in element.get("faces", {}).items():
            if name not in FACE_NAMES or not isinstance(face, dict):
                continue
            reference = resolve_java_texture(textures, str(face.get("texture", "")))
            faces[name] = {"texture": reference, "uv": face.get("uv", [0, 0, 16, 16])}
        obj = create_cube(
            f"Element:{index}", element.get("from", [0, 0, 0]), element.get("to", [16, 16, 16]),
            faces, catalog, (16, 16), False,
        )
        rotation = element.get("rotation")
        if isinstance(rotation, dict) and rotation.get("angle"):
            pivot = bpy.data.objects.new(obj.name + ":pivot", None)
            pivot.location = model_to_blender(rotation.get("origin", [8, 8, 8]))
            bpy.context.collection.objects.link(pivot)
            parent_keep_world(obj, pivot)
            values = [0.0, 0.0, 0.0]
            axis = {"x": 0, "y": 1, "z": 2}.get(str(rotation.get("axis", "y")), 1)
            values[axis] = float(rotation["angle"])
            pivot.rotation_euler = model_rotation(values)
            created.append(pivot)
        created.append(obj)
    return created


def import_bbmodel(data: dict[str, Any], catalog: TextureCatalog) -> list[bpy.types.Object]:
    elements = [item for item in data.get("elements", []) if isinstance(item, dict)]
    texture_entries = data.get("textures", []) if isinstance(data.get("textures"), list) else []
    objects: dict[str, bpy.types.Object] = {}
    created: list[bpy.types.Object] = []
    for index, element in enumerate(elements):
        if element.get("type", "cube") != "cube":
            continue
        faces = {}
        for name, face in element.get("faces", {}).items():
            if name not in FACE_NAMES or not isinstance(face, dict) or face.get("texture") is None:
                continue
            texture_ref = str(face.get("texture", ""))
            try:
                entry = texture_entries[int(texture_ref)]
                texture_ref = str(entry.get("relative_path") or entry.get("path") or entry.get("name") or texture_ref)
            except (ValueError, IndexError, TypeError):
                pass
            faces[name] = {"texture": texture_ref, "uv": face.get("uv", [0, 0, 16, 16])}
        inflate = float(element.get("inflate", 0))
        lower = [float(value) - inflate for value in element.get("from", [0, 0, 0])]
        upper = [float(value) + inflate for value in element.get("to", [16, 16, 16])]
        obj = create_cube(str(element.get("name", f"Element:{index}")), lower, upper, faces, catalog, (16, 16))
        key = str(element.get("uuid", index))
        objects[key] = obj
        rotation = element.get("rotation", [0, 0, 0])
        if any(float(value) for value in rotation):
            pivot = bpy.data.objects.new(obj.name + ":pivot", None)
            pivot.location = model_to_blender(element.get("origin", [0, 0, 0]))
            bpy.context.collection.objects.link(pivot)
            parent_keep_world(obj, pivot)
            pivot.rotation_euler = model_rotation(rotation)
            created.append(pivot)
        created.append(obj)

    def groups(nodes: list[Any], parent: bpy.types.Object | None = None) -> None:
        for node_data in nodes:
            if isinstance(node_data, str):
                if node_data in objects and parent:
                    parent_keep_world(objects[node_data], parent)
                continue
            if not isinstance(node_data, dict):
                continue
            node = bpy.data.objects.new("Group:" + str(node_data.get("name", "unnamed")), None)
            node.location = model_to_blender(node_data.get("origin", [0, 0, 0]))
            bpy.context.collection.objects.link(node)
            if parent:
                parent_keep_world(node, parent)
            groups(node_data.get("children", []), node)
            node.rotation_euler = model_rotation(node_data.get("rotation", [0, 0, 0]))
            created.append(node)

    groups(data.get("outliner", []))
    return created


def has_image_texture(material: bpy.types.Material | None) -> bool:
    return bool(material and material.use_nodes and any(node.type == "TEX_IMAGE" for node in material.node_tree.nodes))


def import_mesh(path: Path, catalog: TextureCatalog) -> list[bpy.types.Object]:
    before = set(bpy.data.objects)
    suffix = path.suffix.lower()
    if suffix in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif suffix == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(path))
        else:
            bpy.ops.import_scene.obj(filepath=str(path))
    elif suffix == ".blend":
        with bpy.data.libraries.load(str(path), link=False) as (source, target):
            target.objects = source.objects
        for obj in target.objects:
            if obj and not obj.users_collection:
                bpy.context.collection.objects.link(obj)
    else:
        raise RuntimeError(f"Unsupported mesh format: {suffix}")
    created = [obj for obj in bpy.data.objects if obj not in before]
    fallback = catalog.resolve()
    if fallback:
        fallback_material = catalog.material()
        for obj in created:
            if obj.type != "MESH":
                continue
            if not obj.data.materials:
                obj.data.materials.append(fallback_material)
            else:
                for index, material in enumerate(obj.data.materials):
                    if not has_image_texture(material):
                        obj.data.materials[index] = fallback_material
    return created


def import_model(args: argparse.Namespace, catalog: TextureCatalog) -> list[bpy.types.Object]:
    suffix = args.model.suffix.lower()
    if suffix in {".blend", ".fbx", ".glb", ".gltf", ".obj"}:
        return import_mesh(args.model, catalog)
    data = load_json(args.model)
    if isinstance(data.get("minecraft:block"), dict):
        return import_bedrock_block(data, args, catalog)
    geometries = bedrock_geometries(data)
    if geometries:
        return import_bedrock(data, args, catalog)
    meta = data.get("meta")
    if suffix == ".bbmodel" or (isinstance(meta, dict) and "model_format" in meta):
        return import_bbmodel(data, catalog)
    if any(key in data for key in ("parent", "textures", "elements")):
        return import_java(data, catalog)
    raise RuntimeError(f"Unsupported JSON model; top-level keys: {sorted(data)}")


def descendants(obj: bpy.types.Object) -> list[bpy.types.Object]:
    result = [obj]
    for child in obj.children:
        result.extend(descendants(child))
    return result


def available_bones() -> dict[str, tuple[str, Any]]:
    result: dict[str, tuple[str, Any]] = {}
    for obj in bpy.data.objects:
        if obj.type == "EMPTY" and (obj.name.startswith("Bone:") or obj.name.startswith("Group:")):
            display = obj.name.split(":", 1)[1]
            result[canonical(display)] = (display, obj)
        if obj.type == "ARMATURE" and obj.pose:
            for bone in obj.pose.bones:
                result[canonical(bone.name)] = (bone.name, bone)
    return result


def apply_bones(args: argparse.Namespace) -> None:
    bones = available_bones()
    requested = [*args.hide_bone, *(item.split("=", 1)[0] for item in args.bone_rotation)]
    missing = [name for name in requested if canonical(name) not in bones]
    if missing:
        names = sorted(display for display, _ in bones.values())
        raise RuntimeError(f"Bone/group not found: {missing}. Available: {names}")
    for name in args.hide_bone:
        _, target = bones[canonical(name)]
        if isinstance(target, bpy.types.Object):
            for obj in descendants(target):
                obj.hide_render = True
        else:
            target.scale = (0.0, 0.0, 0.0)
            for obj in bpy.data.objects:
                if obj.parent_type == "BONE" and canonical(obj.parent_bone) == canonical(name):
                    obj.hide_render = True
    for spec in args.bone_rotation:
        if "=" not in spec:
            raise RuntimeError(f"Invalid --bone-rotation {spec!r}; expected bone=x,y,z")
        name, value = spec.split("=", 1)
        _, target = bones[canonical(name)]
        target.rotation_mode = "XYZ"
        target.rotation_euler = model_rotation(triple(value, "bone rotation"))


def root_objects(objects: list[bpy.types.Object]) -> list[bpy.types.Object]:
    supplied = set(objects)
    return [obj for obj in objects if obj.parent not in supplied]


def object_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points: list[Vector] = []
    bpy.context.view_layer.update()
    for obj in objects:
        if obj.type == "MESH" and not obj.hide_render:
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        raise RuntimeError("A structure item contains no visible mesh geometry")
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )


def hide_item_bones(objects: list[bpy.types.Object], names: list[str]) -> None:
    if not names:
        return
    lookup: dict[str, bpy.types.Object] = {}
    for obj in objects:
        if obj.type == "EMPTY" and (obj.name.startswith("Bone:") or obj.name.startswith("Group:")):
            display = re.sub(r"\.\d{3}$", "", obj.name.split(":", 1)[1])
            lookup[canonical(display)] = obj
    missing = [name for name in names if canonical(name) not in lookup]
    if missing:
        available = sorted(obj.name.split(":", 1)[-1] for obj in lookup.values())
        raise RuntimeError(f"Structure item bone/group not found: {missing}. Available: {available}")
    for name in names:
        for obj in descendants(lookup[canonical(name)]):
            obj.hide_render = True


def manifest_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def place_structure_item(
    objects: list[bpy.types.Object],
    name: str,
    position: list[float],
    rotation: list[float],
    vertical_align: str,
) -> bpy.types.Object:
    if len(position) != 3 or len(rotation) != 3:
        raise RuntimeError(f"Structure item {name!r} requires three-value position and rotation arrays")
    root = bpy.data.objects.new("StructureItem:" + name, None)
    bpy.context.collection.objects.link(root)
    for obj in root_objects(objects):
        parent_keep_world(obj, root)
    root.rotation_euler = model_rotation(rotation)
    bpy.context.view_layer.update()
    lower, upper = object_bounds(objects)
    target = model_to_blender((float(position[0]) * 16, float(position[1]) * 16, float(position[2]) * 16))
    current_center = (lower + upper) / 2
    if vertical_align == "bottom":
        vertical_shift = target.z - lower.z
    elif vertical_align == "center":
        vertical_shift = target.z + 8 - current_center.z
    elif vertical_align == "top":
        vertical_shift = target.z + 16 - upper.z
    else:
        raise RuntimeError(f"Unknown vertical_align {vertical_align!r} for structure item {name!r}")
    root.location += Vector((target.x - current_center.x, target.y - current_center.y, vertical_shift))
    bpy.context.view_layer.update()
    return root


def import_structure(args: argparse.Namespace) -> list[bpy.types.Object]:
    manifest = load_json(args.manifest)
    items = manifest.get("blocks")
    if not isinstance(items, list) or not items:
        raise RuntimeError("Structure manifest requires a non-empty 'blocks' array")
    base = args.manifest.parent
    imported: list[bpy.types.Object] = []
    for index, entry in enumerate(items):
        if not isinstance(entry, dict) or not entry.get("model"):
            raise RuntimeError(f"Invalid structure item at index {index}: expected an object with 'model'")
        item_args = copy.copy(args)
        item_args.manifest = None
        item_args.model = manifest_path(base, str(entry["model"]))
        if not item_args.model.is_file():
            raise RuntimeError(f"Structure model not found: {item_args.model}")
        resource_value = entry.get("resource_pack")
        item_args.resource_pack = manifest_path(base, str(resource_value)) if resource_value else None
        item_args.geometry = entry.get("geometry")
        texture_values = entry.get("textures")
        if isinstance(texture_values, str):
            texture_values = [texture_values]
        if not texture_values and item_args.resource_pack:
            texture_values = [str(item_args.resource_pack / "textures" / "blocks")]
        texture_paths = [manifest_path(base, str(value)) for value in (texture_values or [])]
        catalog = TextureCatalog(texture_paths, args.texture_filter, texture_emission(args.lighting))
        objects = import_model(item_args, catalog)
        if not objects:
            raise RuntimeError(f"Structure item created no objects: {item_args.model}")
        hide_item_bones(objects, [str(value) for value in entry.get("hide_bones", [])])
        name = str(entry.get("name") or item_args.model.stem)
        root = place_structure_item(
            objects,
            name,
            [float(value) for value in entry.get("position", [0, 0, 0])],
            [float(value) for value in entry.get("rotation", [0, 0, 0])],
            str(entry.get("vertical_align", "bottom")),
        )
        imported.extend([*objects, root])
    return imported


def apply_model_rotation(objects: list[bpy.types.Object], value: str) -> bpy.types.Object:
    root = bpy.data.objects.new("DoriosRenderRoot", None)
    bpy.context.collection.objects.link(root)
    for obj in root_objects(objects):
        parent_keep_world(obj, root)
    root.rotation_euler = model_rotation(triple(value, "model rotation"))
    return root


def visible_bounds(exclude: set[bpy.types.Object] | None = None) -> tuple[Vector, Vector]:
    exclude = exclude or set()
    points = []
    bpy.context.view_layer.update()
    for obj in bpy.context.scene.objects:
        if obj in exclude or obj.type != "MESH" or obj.hide_render:
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        raise RuntimeError("The imported model contains no visible mesh geometry")
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )


def view_angles(name: str, azimuth: float, elevation: float) -> tuple[float, float]:
    mapping = {
        "iso-ne": (45, 30), "iso-nw": (135, 30), "iso-sw": (225, 30), "iso-se": (315, 30),
        "front": (90, 0), "back": (270, 0), "right": (0, 0), "left": (180, 0), "top": (90, 90),
    }
    return (azimuth, elevation) if name == "custom" else mapping[name]


def add_camera(args: argparse.Namespace, lower: Vector, upper: Vector) -> bpy.types.Object:
    center = (lower + upper) / 2
    dimensions = upper - lower
    azimuth, elevation = (math.radians(value) for value in view_angles(args.view, args.azimuth, args.elevation))
    direction = Vector((math.cos(elevation) * math.cos(azimuth), math.cos(elevation) * math.sin(azimuth), math.sin(elevation)))
    distance = max(dimensions.length * 3, 10)
    camera_data = bpy.data.cameras.new("Camera")
    camera_data.type = "ORTHO"
    camera = bpy.data.objects.new("Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = center + direction * distance
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera
    bpy.context.view_layer.update()
    if args.ortho_scale is not None:
        camera_data.ortho_scale = args.ortho_scale
    else:
        inverse = camera.matrix_world.inverted()
        corners = []
        for x in (lower.x, upper.x):
            for y in (lower.y, upper.y):
                for z in (lower.z, upper.z):
                    corners.append(inverse @ Vector((x, y, z)))
        width = max(p.x for p in corners) - min(p.x for p in corners)
        height = max(p.y for p in corners) - min(p.y for p in corners)
        render = bpy.context.scene.render
        aspect = render.resolution_x / max(render.resolution_y, 1)
        camera_data.ortho_scale = max(height, width / aspect) * (1 + 2 * max(args.margin, 0))
    return camera


def hex_color(value: str) -> tuple[float, float, float, float]:
    clean = value.strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(char * 2 for char in clean)
    if len(clean) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", clean):
        raise RuntimeError(f"Invalid background color: {value}; expected transparent or #RRGGBB")
    return tuple(int(clean[index:index + 2], 16) / 255 for index in (0, 2, 4)) + (1.0,)


def add_area(name: str, location: Vector, target: Vector, energy: float, size: float, shadows: bool) -> None:
    light_data = bpy.data.lights.new(name, "AREA")
    light_data.energy = energy
    light_data.shape = "DISK"
    light_data.size = size
    light_data.use_shadow = shadows
    light = bpy.data.objects.new(name, light_data)
    bpy.context.collection.objects.link(light)
    light.location = location
    light.rotation_euler = (target - location).to_track_quat("-Z", "Y").to_euler()


def uses_original_directional_light(lighting: str) -> bool:
    return lighting in {"left_light", "right_light", "studio"}


def texture_emission(lighting: str) -> float:
    return 0.12 if uses_original_directional_light(lighting) else 0.16


def setup_lighting(args: argparse.Namespace, lower: Vector, upper: Vector) -> bpy.types.Object | None:
    center = (lower + upper) / 2
    span = max((upper - lower).length, 1)
    multiplier = span * span / 256
    if args.lighting == "flat":
        add_area("Key", center + Vector((span, -span, span * 2)), center, 9000 * multiplier, span * 2, not args.no_shadows)
    elif args.lighting == "dramatic":
        add_area("Key", center + Vector((span, -span, span * 1.6)), center, 12000 * multiplier, span, not args.no_shadows)
        add_area("Rim", center + Vector((-span, span, span)), center, 5000 * multiplier, span, not args.no_shadows)
    else:
        azimuth, _ = (math.radians(value) for value in view_angles(args.view, args.azimuth, args.elevation))
        camera_horizontal = Vector((math.cos(azimuth), math.sin(azimuth), 0))
        camera_side = Vector((-math.sin(azimuth), math.cos(azimuth), 0))
        if args.lighting == "balanced":
            key_depth = -camera_horizontal * span * 0.25 + Vector((0, 0, span * 1.2))
            add_area("LeftKey", center - camera_side * span * 1.2 + key_depth, center, 2800 * multiplier, span * 1.8, not args.no_shadows)
            add_area("RightKey", center + camera_side * span * 1.2 + key_depth, center, 2800 * multiplier, span * 1.8, not args.no_shadows)
            add_area(
                "FrontFill",
                center + camera_horizontal * span * 1.05 + Vector((0, 0, span * 0.45)),
                center,
                1400 * multiplier,
                span * 2.3,
                not args.no_shadows,
            )
        else:
            key_side = camera_side if args.lighting == "right_light" else -camera_side
            fill_side = -key_side
            key_location = center + key_side * span * math.sqrt(2) + Vector((0, 0, span * 2))
            opposite_location = center - key_side * span * math.sqrt(2) + Vector((0, 0, span * 2))
            fill_location = (
                center
                - camera_horizontal * span * (3 / (2 * math.sqrt(2)))
                + fill_side * span * (1 / (2 * math.sqrt(2)))
                + Vector((0, 0, span))
            )
            add_area("Key", key_location, center, 10000 * multiplier, span * 1.5, not args.no_shadows)
            add_area("OppositeFill", opposite_location, center, 2000 * multiplier, span * 1.5, not args.no_shadows)
            add_area("Fill", fill_location, center, 3500 * multiplier, span * 2, not args.no_shadows)
    ground_enabled = args.ground == "on" or (args.ground == "auto" and args.background != "transparent")
    if not ground_enabled:
        return None
    bpy.ops.mesh.primitive_plane_add(size=span * 5, location=(center.x, center.y, lower.z - span * 0.002))
    ground = bpy.context.object
    ground.name = "Ground"
    material = bpy.data.materials.new("GroundMaterial")
    material.diffuse_color = (0.16, 0.17, 0.19, 1)
    ground.data.materials.append(material)
    return ground


def setup_scene(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = map(int, args.resolution.lower().split("x", 1))
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = args.background == "transparent"
    scene.render.filepath = str(args.output)
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = args.samples
    world = bpy.data.worlds.new("World") if scene.world is None else scene.world
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background:
        background.inputs["Color"].default_value = (0.04, 0.045, 0.055, 1) if args.background == "transparent" else hex_color(args.background)
        if args.lighting == "flat":
            world_strength = 1.0
        elif uses_original_directional_light(args.lighting):
            world_strength = 0.7
        else:
            world_strength = 0.83
        background.inputs["Strength"].default_value = world_strength
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        try:
            scene.view_settings.look = "Medium High Contrast"
        except TypeError:
            pass
    scene.view_settings.exposure = 0.20 if uses_original_directional_light(args.lighting) else 0.42


def clear_startup_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def main() -> None:
    args = arguments()
    if args.model:
        args.model = args.model.resolve()
    else:
        args.manifest = args.manifest.resolve()
    args.output = args.output.resolve()
    clear_startup_scene()
    setup_scene(args)
    if args.manifest:
        imported = import_structure(args)
    else:
        catalog = TextureCatalog(
            [path.resolve() for path in args.textures],
            args.texture_filter,
            texture_emission(args.lighting),
        )
        imported = import_model(args, catalog)
    if not imported:
        raise RuntimeError("The model importer created no objects")
    if args.manifest and (args.hide_bone or args.bone_rotation):
        raise RuntimeError("Use per-item hide_bones/rotation values inside a structure manifest")
    if not args.manifest:
        apply_bones(args)
    apply_model_rotation(imported, args.model_rotation)
    bpy.context.view_layer.update()
    lower, upper = visible_bounds()
    ground = setup_lighting(args, lower, upper)
    add_camera(args, lower, upper)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    print(f"DORIOS_RENDER_OK:{args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"DORIOS_RENDER_ERROR:{exc}", file=sys.stderr)
        raise
