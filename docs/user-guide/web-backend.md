# WEB Backend

The **WEB backend** is an experimental alternative rendering path that
fetches server-rendered RGB images from OMERO.web / WebGateway instead of
raw pixel data via ICE.

## When to use it

| Feature | ICE | WEB |
|---------|-----|-----|
| Raw pixel values | Yes | No (RGB only) |
| Projections | Client-side (all modes) | Server-side (Slice, MIP, Mean) |
| Large / pyramidal images | Tile-based dask | Not supported |
| Network requirements | ICE port (4064) | HTTP(S) only |

The WEB backend is useful when:

- You only need a visual preview (not quantitative analysis)
- ICE ports are blocked by a firewall
- You want server-side projections without loading full Z-stacks

## Setup

The WEB backend uses `OmeroWebClient` to communicate with OMERO.web.
It reuses credentials from the ICE connection:

```python
from omero_browser_qt import OmeroWebClient

web = OmeroWebClient(host="omero.example.org", port=443, secure=True)
web.login(username, password)
```

In the demo viewer and browser dialog, the WEB client is set up
automatically when you switch to the WEB backend.

## Rendering images

`WebRenderedImageBackend` wraps an image and gateway pair and returns
rendered `QPixmap` objects:

```python
from omero_browser_qt import WebRenderedImageBackend

backend = WebRenderedImageBackend(image_wrapper, gateway)

# Check if a projection mode is supported
backend.supports_projection("MIP")  # True
backend.supports_projection("SUM")  # False — not available server-side

# Render a slice
pixmap = backend.render_pixmap(z=0, t=0, mode="Slice", channels=channels)
```

The `channels` argument should be the channel list from image metadata,
typically `meta["channels"]`.

## Supported projection modes

| Mode | Server endpoint |
|------|----------------|
| `Slice` | Single Z-plane render |
| `MIP` | Maximum intensity projection |
| `Mean` | Mean intensity projection |

Other modes (SUM, Median, Extended Focus, etc.) are not available
server-side and will cause `supports_projection()` to return `False`.

## Limitations

- **Regular images only** — large/pyramidal images fall back to ICE
- **No raw pixel access** — data is always rendered RGB
- Requires OMERO.web to be running and accessible

## See also

- [API Reference — Web Client](../api/webclient.md)
- [API Reference — View Backends](../api/view_backends.md)
