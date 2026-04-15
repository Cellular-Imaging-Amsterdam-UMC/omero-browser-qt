# Display Settings

omero-browser-qt provides helpers for normalizing OMERO channel display
metadata into a viewer-friendly format.

## Channel display info

`get_image_display_settings()` converts raw OMERO metadata into
`ImageDisplaySettings`, a simple container of `ChannelDisplay` objects:

```python
from omero_browser_qt import get_image_display_settings, load_image_data

result = load_image_data(image_wrapper)
display = get_image_display_settings(result["metadata"])

for ch in display.channels:
    print(ch.name, ch.color, ch.active)
    print(f"  window: {ch.window_start} – {ch.window_end}")
```

### ChannelDisplay fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Channel label from OMERO |
| `color` | `str` | Hex colour string, e.g. `"#00FF00"` |
| `active` | `bool` | Whether the channel is on by default |
| `window_start` | `float` | Display range lower bound |
| `window_end` | `float` | Display range upper bound |
| `emission_wavelength` | `float \| None` | Emission wavelength in nm |
| `coefficient` | `float` | Blending coefficient (default 1.0) |

## Scale bar

`compute_scale_bar()` calculates an appropriate scale bar for overlay
on an image viewer:

```python
from omero_browser_qt import compute_scale_bar

spec = compute_scale_bar(
    pixel_size_um=0.325,
    screen_pixels_per_image_pixel=2.0,
)
if spec is not None:
    print(spec.label)          # e.g. "50 µm"
    print(spec.screen_pixels)  # length in screen pixels
```

Returns `None` when physical pixel size is not available.

## See also

- [API Reference — Rendering](../api/rendering.md)
- [API Reference — Scale Bar](../api/scale_bar.md)
