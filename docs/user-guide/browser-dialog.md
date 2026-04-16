# Browser Dialog

The `OmeroBrowserDialog` is a QuPath-style resizable dialog for browsing
projects, datasets, and images on an OMERO server.

<p align="center">
  <img src="../images/browser.png" alt="Browser dialog" width="700">
</p>

## Layout

The dialog is divided into:

- **Header bar** — shows the connected server and username
- **Left panel** — group/owner filter combos, a lazy-loading project → dataset → image tree, and a name filter
- **Right panel** — 256 × 256 thumbnail preview and an attribute table (dimensions, pixel sizes, acquisition date, …)
- **Footer** — *Import* button to confirm the selection

## Quick selection API

The simplest way to use the dialog is via the class-level convenience methods.
These handle login, browsing, and returning selected images in one call:

```python
from omero_browser_qt import OmeroBrowserDialog

# Returns a list of OMERO ImageWrapper objects
images = OmeroBrowserDialog.select_images()

# Returns SelectedImageContext objects with breadcrumb info
contexts = OmeroBrowserDialog.select_image_contexts()
```

Both methods accept an optional `gateway` argument. If omitted, a new
`OmeroGateway` singleton is created automatically.

## Lower-level API

For full control over dialog lifetime:

```python
from omero_browser_qt import OmeroGateway, LoginDialog, OmeroBrowserDialog

gw = OmeroGateway()
if LoginDialog(gateway=gw).exec():
    dlg = OmeroBrowserDialog(gateway=gw)
    if dlg.exec():
        images = dlg.get_selected_images()
```

## Multi-selection

Hold **Ctrl** (or **Cmd** on macOS) to select multiple images in the tree.
All selected images are returned by `get_selected_images()`.

## Filtering

Type in the **Filter** field at the bottom of the tree panel to filter
images by name. The filter applies to the currently expanded datasets.

## Selection context backend

`SelectedImageContext.backend` is retained for compatibility and always
returns `"ICE"`.
