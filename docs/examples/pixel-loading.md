# Pixel Loading Examples

## Load a full image

```python
from omero_browser_qt import load_image_data, get_image_display_settings

result = load_image_data(image_wrapper)
volumes = result["images"]     # list[np.ndarray], one per channel
meta    = result["metadata"]

# Get display settings
display = get_image_display_settings(meta)
for ch in display.channels:
    print(f"{ch.name}: {ch.color}, active={ch.active}")
    print(f"  contrast: {ch.window_start} – {ch.window_end}")
```

## Browse slices interactively

```python
from omero_browser_qt import RegularImagePlaneProvider

provider = RegularImagePlaneProvider(image_wrapper)

# Single plane
plane = provider.get_plane(c=0, z=10, t=0)  # (Y, X)

# Full Z-stack for one channel
stack = provider.get_stack(c=0, t=0)         # (Z, Y, X)
```

## Large / pyramidal images

```python
from omero_browser_qt import is_large_image, load_image_lazy

if is_large_image(image_wrapper):
    result = load_image_lazy(image_wrapper)
    dask_arrays = result["images"]
    # dask_arrays[channel_idx][resolution_level] → dask.array
    full_res = dask_arrays[0][0].compute()  # materialise level 0
```

## Scale bar overlay

```python
from omero_browser_qt import compute_scale_bar

spec = compute_scale_bar(
    pixel_size_um=meta["pixel_size_x"],
    screen_pixels_per_image_pixel=2.0,
)
if spec:
    print(f"Draw {spec.screen_pixels}px bar labelled '{spec.label}'")
```

## Combine with WEB rendering

```python
from omero_browser_qt import WebRenderedImageBackend

backend = WebRenderedImageBackend(image_wrapper, gateway)
pixmap = backend.render_pixmap(z=0, t=0, mode="MIP",
                               channels=meta["channels"])
# pixmap is a QPixmap ready for display
```
