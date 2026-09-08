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

For blocks with visual material states in permutations, use `--material-permutation base --output-suffix _off` for the component textures and `--material-permutation last --output-suffix _on` for the last material-bearing permutation. This produces both states without editing the source block definitions.

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
- `--bone-position "bone=x,y,z"` to add a model-space translation to a JSON/BBMODEL bone/group; repeat as needed
- `--bone-scale "bone=x,y,z"` to set a JSON/BBMODEL bone/group scale around its pivot; repeat as needed
- `--geometry <identifier>` to choose one geometry from a multi-geometry Bedrock file
- `--material-permutation auto|base|last` to choose the Bedrock block material state
- `--resource-pack <RP-folder>` to resolve Bedrock block geometry and `terrain_texture.json`; inferred for sibling `BP`/`RP` packs
- `--ortho-scale <number>` to override automatic framing
- `--resolution WIDTHxHEIGHT` for the delivered PNG (default `80x80`)
- `--render-resolution WIDTHxHEIGHT` for the Blender source (automatic `1024x1024` for the default output)
- `--source-output <hd.png>` to preserve that Blender source beside the scaled result
- `--no-center-content` to preserve the raw camera position instead of centering visible alpha bounds
- `--margin <fraction>` (default `0.025`; `vanilla` uses `0.0075` when omitted), `--samples <number>`
- `--background transparent|#RRGGBB`, `--lighting vanilla|balanced|left_light|right_light|studio|flat|dramatic|neon`
- `--ground auto|on|off`, `--no-shadows`, `--texture-filter vanilla|closest|linear` (`vanilla` is retained as a compatibility alias of `closest`)
- `--blender <path>` when Blender is not on `PATH`
- `--dry-run` to print the Blender command without executing it

Use `closest` texture filtering by default for Minecraft and other pixel art. Keep texture colors unchanged; do not repaint, upscale, hallucinate, or complete missing pixels.
Use `vanilla` by default for Minecraft recipe-book and inventory-style block icons. It uses unlit texture materials, deterministic per-face shading, the Standard color transform, zero exposure, no world illumination, and no render-engine shadows. This prevents EEVEE, AgX, specular response, or soft lights from shifting the source texture colors.

Use `right_light` for promotional renders. It provides the strong upper-right studio key, softens the darkest face with a secondary light placed in the opposite direction at exactly 20% of the main key energy, and uses `0.20` exposure. `left_light` mirrors every directional light around the camera axis with identical energy, height, softness, fill ratio, material response, and exposure. Use `balanced` only when the prompt requests normalized faces; it uses equal upper-left and upper-right keys plus a weak centered front fill. `studio` remains a backward-compatible alias of `left_light`.

The `vanilla` preset uses a `0.0075` default margin. For synthesized Java cube models it also rotates the top face texture `90` degrees clockwise; Bedrock horizontal-face rotation continues to use `--bedrock-horizontal-uv-rotation`.

Use `neon` for Bedrock PBR packs whose blocks emit colored light. It reads adjacent `.texture_set.json` files, maps MER red/green/blue channels to metalness/emission/roughness, applies normal or height maps when present, and uses the block's `RP/local_lighting/local_lighting.json` color for a restrained emissive fill and rim. Non-emissive pixels stay physically lit instead of glowing uniformly.

### Stable vanilla block batch

Generate obtainable vanilla block icons from a synchronized
`Mojang/bedrock-samples` checkout:

```powershell
python <skill-dir>/scripts/render_vanilla_blocks.py `
  --source-root <bedrock-samples-folder> `
  --output <render-folder> `
  --overrides <jei-project>/tools/recipe-overrides.json `
  --jobs 4
```

The command renders 80x80 PNGs with the `vanilla` preset. It excludes blocks
that use item sprites, synthesizes common simple shapes, and writes
`render-report.json` containing item-like, technical, unresolved, and complex
geometry groups. The batch keeps a fixed full-block orthographic scale so
partial shapes preserve their relative size, maps directional fronts to the
right-hand visible face, and uses carried textures except for documented
placed-texture exceptions. Use `--skip-existing` to resume. Use
`--prune-stale` after changing classification so exact candidate PNGs that
became item-like, technical, or complex are removed from the output folder.

Vertical block textures are treated as flipbooks: retain the real image
dimensions in each face specification and propagate that `texture_size`
through the Java-model importer so UV `0..16` selects only the first 16x16
frame. Heavy Core uses its three 8x8 regions from the shared 16x16 atlas.
Keep floor-mounted partial shapes such as carpets, pressure plates, and closed
trapdoors at block Y=0. Render and alpha-center them normally, then translate
the finished 80x80 PNG downward by 17 pixels without resampling. This preserves
their approved shading while placing them near the bottom of the full-block
frame; do not alter framing with transparent geometry. Shulker
Boxes are not synthetic cubes; render
`models/entity/shulker_v1.0.geo.json` as `geometry.shulker` in its closed
base pose with the matching `textures/entity/shulker/shulker_<color>` atlas.
Hide the internal `head` bone and slightly inset the base in X/Z to avoid
coplanar overlap with the lid without creating a horizontal gap.
Fences, Fence Gates, and Walls must use the bundled Bedrock inventory
geometries in `assets/vanilla_inventory_models/`; do not synthesize these
families from generic boxes. The vanilla batch selects
`geometry.fence_inventory`, `geometry.fence_gate_inventory`, or
`geometry.wall_inventory` and applies the resolved side texture at the fixed
full-block orthographic scale.

### Stable vanilla entity batch

Generate one canonical idle render for every spawn-egg mob in a synchronized
`Mojang/bedrock-samples` checkout:

```powershell
python <skill-dir>/scripts/render_vanilla_entities.py `
  --source-root <bedrock-samples-folder> `
  --output <render-folder> `
  --jobs 4
```

The command defaults to `--catalog mobs --pose curated`: projectiles,
vehicles, dropped/technical entities, and other definitions without a spawn
egg are excluded. Use `--catalog all` only when those client entities are
explicitly requested.

The batch renders transparent 80x80 PNGs with the `vanilla` preset and writes
`render-report.json`. It prefers the unversioned client definition, otherwise
the highest `_vN` definition, resolves modern `.geo.json` and legacy
`models/mobs.json` geometries, and flattens legacy geometry inheritance into
temporary files outside the source assets. Most mobs retain their standing
bind pose. A small curated table applies static setup/idle transforms where
the published bind geometry is an assembly pose, hides render-controller parts
that are false in the canonical state, selects canonical textures such as the
wild ocelot and elder guardian, composites the wandering-trader llama decor,
and makes visible Blaze/Glow Squid atlas pixels fully opaque. The curated
renderer also preserves Bedrock's signed X/Z bone rotations, distinguishes
absolute pivot targets from relative animation offsets, and combines Breeze's
body, eyes, and three wind render-controller passes at their authored origin.
Use
`--pose bind` for raw geometry or `--pose auto` for the generic animation
heuristic.

Each unique identifier gets one primary geometry and texture; all selections,
pose transforms, hidden parts, texture layers, and warnings are recorded in
the report. If Mojang's samples omit a referenced geometry, report the entity
as unresolved rather than synthesizing a substitute model. Use
`--skip-existing` to resume, `--include` for a comma-separated focused run,
or `--classify-only` to inspect resolution without invoking Blender.

For villager profession variants, render the same curated
`geometry.villager_v2` pose with a 64x64 atlas composited in runtime order:
adult base, one biome layer, one profession layer, and one level badge. Treat
armorer, butcher, cartographer, cleric, farmer, fisherman, fletcher,
leatherworker, librarian, shepherd, stonemason, toolsmith, and weaponsmith as
the working professions; nitwit and unskilled are non-job variants. Bake a
`[-90, 0, 0]` bind-pose rotation into the adult `brim` bone before rendering;
the published adult geometry omits the rotation present in the equivalent
zombie-villager geometry, otherwise wide hats render vertically in the face.
Use `scripts/render_villager_professions.py` for this workflow.

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
