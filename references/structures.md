# Multi-block structure manifests

Use grid positions in block units. Treat `[x, y, z]` as horizontal X, vertical Y, and horizontal Z; adjacent cells differ by `1`. The renderer normalizes each model into its cell by horizontal center and bottom height, so centered custom geometry and `0..16` full cubes align correctly.

```json
{
  "blocks": [
    {
      "name": "terminal",
      "model": "../Addon/BP/blocks/terminal.json",
      "resource_pack": "../Addon/RP",
      "position": [0, 0, 0]
    },
    {
      "name": "cable",
      "model": "../Addon/BP/blocks/cable.json",
      "resource_pack": "../Addon/RP",
      "position": [1, 0, 0],
      "hide_bones": ["up", "down", "north", "south"]
    }
  ]
}
```

Optional per-item fields:

- `textures`: one image/directory or an array; default `<resource_pack>/textures/blocks`.
- `rotation`: model X,Y,Z degrees; default `[0,0,0]`.
- `geometry`: explicit geometry identifier for multi-geometry files.
- `hide_bones`: group/bone names hidden only on that item. Placement is anchored from the complete model before those bones are hidden, so asymmetric T/corner pipes remain centered in their grid cell.
- `bone_positions`: one `"bone=x,y,z"` string or an array of them; applies additive model-space translations before placement, useful for reproducing a static state from an entity animation.
- `bone_scales`: one `"bone=x,y,z"` string or an array; applies model-space scale around the bone pivot before placement, useful for static beams, pistons, and other animated-length parts.
- `object_positions`: one `"object=x,y,z"` string or an array; applies a model-space offset to a specific generated mesh object such as `tower:cube_17`, useful for a render-only correction without modifying the source model.
- `anchor_visible_bounds`: when `true`, hide optional bones before calculating item placement. Use it for variable-size models whose selected bones define their actual footprint; leave it `false` for asymmetric connection models such as pipes.
- `vertical_align`: `bottom` (default), `center`, or `top` within the 16-unit cell; use `center` for cables or other floating components.
- `bedrock_horizontal_uv_rotation`: override the default 90-degree `up`/`down` Bedrock UV correction for this item.

For repeated items, place shared fields in a top-level `defaults` object. Each block entry inherits those fields and may override any of them, which keeps grid manifests concise.

Render and retain both stages:

```powershell
python <skill-dir>/scripts/render_model.py `
  --manifest <structure.json> `
  --output <structure.png> `
  --source-output <structure_hd.png> `
  --resolution 320x320 `
  --render-resolution 1024x1024
```

Choose the lower resolution so the projected pixels per block remain close to a standalone 80x80 render. Estimate from the isometric projected extents, not merely the number of blocks; keep the high and low canvases at the same aspect ratio.
