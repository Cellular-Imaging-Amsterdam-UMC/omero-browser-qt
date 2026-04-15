# Pixel Loading

omero-browser-qt provides several ways to load pixel data from OMERO images,
all using ICE `RawPixelsStore` for direct access to stored values.

## Full 5-D array

For regular-sized images, fetch all channels at once:

```python
from omero_browser_qt import load_image_data

result = load_image_data(image_wrapper)
volumes = result["images"]     # list of np.ndarray, one per channel
meta    = result["metadata"]   # dict with dimensions, pixel sizes, channels, …
```

Each array has shape `(Z, Y, X)` for single-timepoint images, or
`(T, Z, Y, X)` when multiple timepoints are present.

## Large / pyramidal images

Images exceeding 4096 × 4096 pixels are considered "large" and loaded
lazily via dask:

```python
from omero_browser_qt import is_large_image, load_image_lazy

if is_large_image(image_wrapper):
    result = load_image_lazy(image_wrapper)
    # result["images"]: list[list[dask.array]]
    #   outer list = channels, inner list = resolution levels
```

Tiles are fetched on demand using `getTile()` with a default tile size
of 1024 × 1024.

## Interactive plane provider

For slice-by-slice browsing (e.g. in a viewer), use
`RegularImagePlaneProvider` to fetch only the planes you need:

```python
from omero_browser_qt import RegularImagePlaneProvider

provider = RegularImagePlaneProvider(image_wrapper)
plane = provider.get_plane(c=0, z=5, t=0)   # shape (Y, X)
stack = provider.get_stack(c=0, t=0)         # shape (Z, Y, X)
```

For pyramidal images, use `PyramidTileProvider`:

```python
from omero_browser_qt import PyramidTileProvider

provider = PyramidTileProvider(image_wrapper)
tile = provider.get_tile(resolution=0, c=0, z=0, t=0, x=0, y=0,
                         width=1024, height=1024)
```

## Metadata

`get_image_metadata()` extracts dimensions, pixel type, physical pixel
sizes, and channel information from an OMERO `ImageWrapper`:

```python
from omero_browser_qt import get_image_metadata

meta = get_image_metadata(image_wrapper)
print(meta["size_x"], meta["size_y"], meta["size_z"])
print(meta["pixel_size_x"])   # µm, or None
print(meta["channels"])       # list of dicts with name, color, active, …
```

## See also

- [API Reference — Image Loading](../api/image_loader.md)
- [API Reference — Gateway](../api/gateway.md) for the underlying `raw_pixels_store` context manager
