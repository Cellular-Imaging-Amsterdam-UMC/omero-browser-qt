# Structured Selection

When you need **project → dataset → image** breadcrumb information alongside
the selected images, use `select_image_contexts()`.

```python
from PyQt6.QtWidgets import QApplication
from omero_browser_qt import OmeroBrowserDialog

app = QApplication([])

for ctx in OmeroBrowserDialog.select_image_contexts():
    print(ctx.breadcrumb)
    print(f"  Image: {ctx.image.getName()} (id={ctx.image.getId()})")
    print(f"  Dataset: {ctx.dataset_name} (id={ctx.dataset_id})")
    print(f"  Project: {ctx.project_name} (id={ctx.project_id})")
    print(f"  Backend: {ctx.backend}")
```

## SelectedImageContext fields

Each `SelectedImageContext` carries:

| Field | Type | Description |
|-------|------|-------------|
| `image` | `ImageWrapper` | The OMERO image object |
| `image_id` | `int` | Image ID |
| `image_name` | `str` | Image name |
| `dataset_id` | `int \| None` | Parent dataset ID |
| `dataset_name` | `str \| None` | Parent dataset name |
| `project_id` | `int \| None` | Grandparent project ID |
| `project_name` | `str \| None` | Grandparent project name |
| `breadcrumb` | `str` | Human-readable path, e.g. `"Project > Dataset > image.tiff"` |
| `backend` | `str` | Always `"ICE"` (retained for compatibility) |

## When to use this

Use structured selection when your application needs to:

- Display the provenance of selected images
- Organize outputs by project/dataset
- Log or record which dataset an image came from
