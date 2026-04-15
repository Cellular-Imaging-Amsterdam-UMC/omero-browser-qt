# Simple Selection

The quickest way to browse and select one or more images from OMERO.

```python
from PyQt6.QtWidgets import QApplication
from omero_browser_qt import OmeroGateway, OmeroBrowserDialog

app = QApplication([])
gw = OmeroGateway()

# Login dialog → browser dialog → returns selected ImageWrappers
images = OmeroBrowserDialog.select_images(gateway=gw)

for img in images:
    print(f"{img.getName()} (id={img.getId()})")

gw.disconnect()
```

`select_images()` is a blocking class method that:

1. Opens the login dialog (if not already connected)
2. Opens the browser dialog
3. Returns a `list` of OMERO `ImageWrapper` objects when the user clicks *Import*
4. Returns an empty list if the user cancels

## Using an existing gateway

If your application already has a connected gateway, pass it in to skip
the login dialog:

```python
images = OmeroBrowserDialog.select_images(gateway=my_gateway)
```

## Lower-level control

For finer control over dialog lifetime, instantiate the dialog directly:

```python
from omero_browser_qt import OmeroGateway, LoginDialog, OmeroBrowserDialog

gw = OmeroGateway()

if LoginDialog(gateway=gw).exec():
    dlg = OmeroBrowserDialog(gateway=gw)
    if dlg.exec():
        for img in dlg.get_selected_images():
            print(img.getName())
```
