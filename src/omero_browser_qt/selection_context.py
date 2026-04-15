"""Selection context helpers for OMERO browser selections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SelectedImageContext:
    """Structured selection result for a single OMERO image.

    Returned by :meth:`OmeroBrowserDialog.select_image_contexts` and
    :meth:`OmeroBrowserDialog.get_selected_image_contexts`.  Contains
    the full hierarchy path (group → owner → project → dataset → image)
    so callers can display breadcrumbs or organise files.

    Attributes
    ----------
    image : object
        The OMERO ``ImageWrapper`` object.
    image_id : int | None
        OMERO image id.
    image_name : str
        Image name (display text).
    group_id, group_name : int | None, str | None
        OMERO group the image belongs to.
    owner_id, owner_name : int | None, str | None
        Owner / experimenter of the image.
    project_id, project_name : int | None, str | None
        Parent project (if any).
    dataset_id, dataset_name : int | None, str | None
        Parent dataset (if any).
    path_labels : tuple[str, ...]
        Ordered labels from group down to image for breadcrumb display.
    backend : str
        ``"ICE"`` or ``"WEB"`` — the backend selected by the user.
    """

    image: Any
    image_id: int | None
    image_name: str
    group_id: int | None = None
    group_name: str | None = None
    owner_id: int | None = None
    owner_name: str | None = None
    project_id: int | None = None
    project_name: str | None = None
    dataset_id: int | None = None
    dataset_name: str | None = None
    path_labels: tuple[str, ...] = ()
    backend: str = "ICE"

    @property
    def breadcrumb(self) -> str:
        return " / ".join(part for part in self.path_labels if part)

