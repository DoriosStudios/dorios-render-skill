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
- `hide_bones`: group/bone names hidden only on that item.
- `vertical_align`: `bottom` (default), `center`, or `top` within the 16-unit cell; use `center` for cables or other floating components.

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
