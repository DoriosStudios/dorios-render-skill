# Model and texture notes

Use this reference only to troubleshoot a format or explain a limitation.

## Contents

- [Format detection](#format-detection)
- [Texture discovery](#texture-discovery)
- [Coordinates and UVs](#coordinates-and-uvs)
- [Known limitations](#known-limitations)

## Format detection

The scripts inspect JSON structure instead of relying only on extensions:

- Bedrock geometry: `minecraft:geometry`, `geometry.*`, or a geometry object containing `bones` and `description`.
- Bedrock block definition: top-level `minecraft:block`; resolve its geometry and material instances using the sibling `RP` folder.
- Blockbench: `meta.model_format`, `elements`, and usually `outliner`.
- Java block model: `parent`, `textures`, and/or `elements` whose cubes contain named faces.
- Unknown JSON: stop and print the top-level keys. Do not assume it is Bedrock.

A Bedrock file may contain several geometries. Use `inspect_model.py` to list identifiers and pass the selected value with `--geometry`.

## Texture discovery

The renderer builds an index from each file/directory passed to `--textures`. Matching ignores case, file extension, common prefixes (`textures/`, `blocks/`, `entity/`), and separators. Resolution order is:

1. exact normalized relative reference;
2. exact filename stem;
3. suffix match;
4. the only supplied image, when unambiguous.

Bedrock geometry usually uses one atlas PNG whose dimensions correspond to `texture_width` and `texture_height`. Supply it directly when possible.

Java block models resolve `#variables` recursively through the model's `textures` map. For inherited parent JSON outside `cube_all`, `cube`, and `cube_column`, provide a flattened model containing `elements` or also provide the needed parent model for a future extension.

Imported GLB/GLTF/BLEND files keep embedded textures. OBJ files should include the adjacent `.mtl` and its images; otherwise the first supplied image is used as a fallback material.

## Coordinates and UVs

Minecraft/Blockbench model coordinates use Y-up; Blender uses Z-up. The renderer maps model `(x, y, z)` to Blender `(x, -z, y)`. Model rotations are similarly remapped.

JSON UV coordinates start at the image's upper-left while Blender UV V starts at the lower-left, so V is inverted during mesh creation. `closest` interpolation preserves hard pixel edges.

Bedrock box UVs are expanded into six face rectangles using the cube dimensions. Explicit per-face UV data takes precedence.

## Known limitations

- Bedrock poly meshes, locators, animations, material instances, and render controllers are not evaluated.
- Java multipart/blockstate selection is outside the model file; provide the selected model JSON.
- Blockbench mesh elements and animation timelines are not evaluated; cube elements and group hierarchy are supported.
- Geometry deformation, skinning, and constraints in imported armatures depend on Blender's importer and the source file.
- Transparent pixels render correctly, but semitransparent sorting can differ from a specific game engine.
- The lighting emulates a clean asset screenshot, not the exact shader pipeline of Minecraft or another game.
