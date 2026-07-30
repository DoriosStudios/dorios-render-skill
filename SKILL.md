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
4. Use the canonical default final canvas: `80x80`, square aspect ratio, automatic orthographic framing, `0.14` margin, and transparent background. Preserve these proportions and empty margins unless the user explicitly requests a different resolution, margin, aspect ratio, framing, or background.
5. Render the default image at `1024x1024` in Blender, then reduce it to `80x80` with the bundled exact Nearest Neighbour resizer. Never render directly at 80x80 and never use bilinear, bicubic, antialiased, or browser/CSS resizing for the delivered PNG.
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
- `--margin <fraction>` (default `0.14`), `--samples <number>`
- `--background transparent|#RRGGBB`, `--lighting studio|flat|dramatic`
- `--ground auto|on|off`, `--no-shadows`, `--texture-filter closest|linear`
- `--blender <path>` when Blender is not on `PATH`
- `--dry-run` to print the Blender command without executing it

Use `closest` texture filtering by default for Minecraft and other pixel art. Keep texture colors unchanged; do not repaint, upscale, hallucinate, or complete missing pixels.

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

- Bedrock `.geo.json` / geometry JSON: preserve cube dimensions, pivots, hierarchy, base rotations, mirror flags, and UV coordinates where represented.
- Bedrock behavior-pack block JSON/JSONC: read `minecraft:geometry`; use per-face `minecraft:material_instances` or legacy `RP/blocks.json` textures; resolve texture keys and dependency geometries through sibling resource packs.
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
