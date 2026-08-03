---
name: dorios-render-skill
description: Render deterministic, non-generative images from 3D models and pixel textures with Blender. Use when Codex must create an isometric or custom-angle PNG from Minecraft Bedrock geometry JSON, Minecraft Java block-model JSON, Blockbench BBMODEL, GLB/GLTF, OBJ, FBX, or BLEND files; inspect available bones; hide selected bones; apply per-bone rotations; preserve pixel-art textures; or simulate a clean in-game inventory/screenshot render without using AI image generation.
---

# Dorios Render Skill

Create a reproducible Blender render from the supplied geometry and textures. Never use an image-generation model as a fallback: the output must come from the model data, texture pixels, camera, and lights.

## Workflow

1. Resolve the model, texture, and output paths from the user's message or attachments. Search the current workspace when a basename is supplied. Never guess between multiple plausible models; list the matches and ask which one to use.
2. Inspect JSON/BBMODEL inputs before rendering when the user mentions bones, wants to choose parts, or the geometry identifier is ambiguous:

   ```powershell
   python <skill-dir>/scripts/inspect_model.py --model <model>
   ```

3. Translate natural-language view requests using the view table below. Use `iso-ne` when no view is specified.
4. Use the canonical default final canvas: `80x80`, square aspect ratio, automatic orthographic framing, `0.025` margin, and transparent background. Center the non-zero alpha bounds exactly on both canvas axes without resizing or changing the margins. Preserve these proportions unless the user explicitly requests a different resolution, margin, aspect ratio, framing, background, or disables centering.
5. Render the default image at `1024x1024` in Blender, center its visible alpha bounds in that unchanged HD canvas, then reduce it to `80x80` with the bundled exact Nearest Neighbour resizer. Never render directly at 80x80 and never use bilinear, bicubic, antialiased, or browser/CSS resizing for the delivered PNG.
6. Invoke the launcher. Pass every texture file or one texture directory after `--textures`. Repeat `--hide-bone` and `--bone-rotation` as needed.
7. Verify that the PNG exists, has the requested dimensions, and visually inspect it at an integer zoom level when an image-viewing tool is available. If the view hides an important feature, rerender from a better angle while preserving the user's explicit choices.
8. Return the PNG and state the chosen view, hidden bones, and any format limitation that affected the result.

For every block in a Bedrock add-on containing sibling `BP` and `RP` folders, use:

```powershell
python <skill-dir>/scripts/render_bedrock_pack.py `
  --pack-root <addon-folder> `
  --output <render-folder> `
  --view iso-ne
```

The batch renderer loads both `RP/textures/blocks` and `RP/textures/entity`. This is required for custom geometries whose secondary material instances use entity textures, such as the `fluid` or `gas` interior bones of resource tanks.

For large packs, repeat `--exclude-path <fragment>` to omit model families, repeat `--include-path <fragment>` to render only selected families, and use `--include-identifier-suffix <suffix>` or `--exclude-identifier-suffix <suffix>` for identifier-based selection such as rendering only `_seeds` definitions from a crop folder. Use `--jobs <count>` for controlled concurrent Blender processes, and add `--skip-existing` to resume an interrupted batch without replacing completed PNGs. The batch renderer rejects duplicate identifier-derived filenames before rendering.

The batch renderer automatically loads `<pack-root>/Assets/render_overrides.json` when present, or accepts `--overrides <file>`. Use `path_rules` for family-wide settings and `blocks` keyed by full identifier for `model_rotation`, `ortho_scale`, `margin`, `resolution`, `view`, `lighting`, `hide_bones`, or `bone_rotations` exceptions. This keeps verified pack-specific framing, state visibility, and orientation reproducible.

For a placed multi-block composition, create a temporary JSON/JSONC manifest and pass `--manifest` instead of `--model`. Read [references/structures.md](references/structures.md) before creating the manifest. Use `--source-output <name_hd.png>` to retain the high-resolution Blender source while `--output` receives the Nearest Neighbour result.

## Render command

```powershell
python <skill-dir>/scripts/render_model.py `
  --model <model-path> `
  --textures <texture-file-or-directory> `
  --output <output.png> `
  --view iso-ne `
  --background transparent
```

Useful options:

- `--view iso-ne|iso-nw|iso-se|iso-sw|front|back|left|right|top|custom`
- `--azimuth <degrees> --elevation <degrees>` for `custom`
- `--model-rotation x,y,z` to rotate the complete model in degrees
- `--hide-bone <name>` to hide a bone/group and its descendants; repeat as needed
- `--bone-rotation "bone=x,y,z"` to override a bone/group Euler rotation; repeat as needed
- `--geometry <identifier>` to choose one geometry from a multi-geometry Bedrock file
- `--resource-pack <RP-folder>` to resolve Bedrock block geometry and `terrain_texture.json`; inferred for sibling `BP`/`RP` packs
- `--ortho-scale <number>` to override automatic framing
- `--resolution WIDTHxHEIGHT` for the delivered PNG (default `80x80`)
- `--render-resolution WIDTHxHEIGHT` for the Blender source (automatic `1024x1024` for the default output)
- `--source-output <hd.png>` to preserve that Blender source beside the scaled result
- `--no-center-content` to preserve the raw camera position instead of centering visible alpha bounds
- `--margin <fraction>` (default `0.025`), `--samples <number>`
- `--background transparent|#RRGGBB`, `--lighting balanced|left_light|right_light|studio|flat|dramatic`
- `--ground auto|on|off`, `--no-shadows`, `--texture-filter closest|linear`
- `--blender <path>` when Blender is not on `PATH`
- `--dry-run` to print the Blender command without executing it

Use `closest` texture filtering by default for Minecraft and other pixel art. Keep texture colors unchanged; do not repaint, upscale, hallucinate, or complete missing pixels.
Use `right_light` by default. It provides the strong upper-right studio key, softens the darkest face with a secondary light placed in the opposite direction at exactly 20% of the main key energy, and uses `0.20` exposure. `left_light` mirrors every directional light around the camera axis with identical energy, height, softness, fill ratio, material response, and exposure. Use `balanced` only when the prompt requests normalized faces; it uses equal upper-left and upper-right keys plus a weak centered front fill. `studio` remains a backward-compatible alias of `left_light`.

## View mapping

| Request | View | Meaning |
|---|---|---|
| isometric, default | `iso-ne` | front/right/top-style three-quarter view |
| opposite isometric | `iso-sw` | rear/left/top-style three-quarter view |
| front-left | `iso-nw` | front/left/top-style three-quarter view |
| back-right | `iso-se` | rear/right/top-style three-quarter view |
| front/back/left/right/top | matching value | orthographic cardinal view |
| exact angle | `custom` | pass azimuth and elevation explicitly |

If the user's coordinate convention differs, render a low-resolution preview and adjust the azimuth by 90 or 180 degrees. Do not silently change an explicitly supplied angle.

## Bones and posing

- Inspect first so names come from the actual model.
- Match bone/group names case-insensitively and normalize spaces, `_`, `-`, and namespace prefixes.
- Hiding a Bedrock or Blockbench group hides all geometry below it.
- For armature formats, apply rotations to pose bones. For JSON/BBMODEL, apply them to generated group pivots.
- Treat `--bone-rotation` values as degrees in the model's X,Y,Z convention.
- If a named bone is absent, stop and report the available close matches; never pretend the pose was applied.

## Format routing

- Bedrock `.geo.json` / geometry JSON: preserve cube dimensions, pivots, hierarchy, base rotations, mirror flags, and UV coordinates where represented. Map horizontal `up`/`down` face UVs with atlas U along the model X axis so directional pipe textures do not appear quarter-turned.
- Bedrock behavior-pack block JSON/JSONC: read `minecraft:geometry`; use per-face and per-cube `minecraft:material_instances` or legacy `RP/blocks.json` textures; resolve texture keys and dependency geometries through sibling resource packs. Scale UV coordinates to each material texture's actual atlas dimensions and respect `uv_rotation` when a geometry mixes atlas sizes. This prevents crop X-planes and similar thin geometry from wrapping or repeating.
- Bedrock blocks whose materials exist only in `permutations`: for a catalog render, merge the last material-bearing permutation over the base components so staged blocks such as crops appear in their mature/complete state.
- Java block-model JSON: render explicit `elements`; synthesize standard cube parents such as `cube_all` and `cube_column`; resolve texture variables from supplied texture paths.
- `.bbmodel`: render cube elements and outliner groups. Warn if the file relies on unsupported mesh elements, animations, or display transforms.
- `.glb`, `.gltf`, `.obj`, `.fbx`, `.blend`: import using Blender. Preserve embedded materials; apply the supplied texture only to material slots that have no image texture.

Read [references/formats.md](references/formats.md) only when diagnosing texture resolution, JSON format ambiguity, unsupported features, or coordinate/UV differences.

## Failure rules

- If Blender is missing, report that Blender 3.6+ must be installed or passed with `--blender`; do not create a substitute image.
- If textures are missing, inspect model references and search next to the model before asking the user.
- If the model imports but has no visible geometry, rerun inspection and report the precise unsupported structure.
- Never overwrite a source model or texture. The only normal output is the requested PNG.
- Keep temporary Blender files out of the source asset directory.
