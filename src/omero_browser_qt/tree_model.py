"""
OmeroTreeModel — lazy-loading QStandardItemModel for the OMERO hierarchy.

Hierarchy:  Project  →  Dataset  →  Image
            (plus orphaned datasets and orphaned images at root level)

Children are fetched on demand (when the user expands a node) via the
standard ``canFetchMore`` / ``fetchMore`` pattern so that the tree scales
to large repositories without blocking the UI.
"""

from __future__ import annotations

import logging
import os
from enum import IntEnum, auto
from typing import Any

from PyQt6.QtCore import QModelIndex, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QStandardItem, QStandardItemModel

log = logging.getLogger(__name__)

_ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

# Mapping node-type → icon file name
_NODE_ICON: dict[int, str] = {}  # populated once in _ensure_icons

_icons_loaded = False


def _ensure_icons() -> None:
    global _icons_loaded
    if _icons_loaded:
        return
    _icons_loaded = True
    _NODE_ICON[NodeType.PROJECT] = "folder16.png"
    _NODE_ICON[NodeType.DATASET] = "folder_image16.png"
    _NODE_ICON[NodeType.IMAGE] = "image16.png"
    _NODE_ICON[NodeType.ORPHANED_DATASETS] = "folder_yellow16.png"
    _NODE_ICON[NodeType.ORPHANED_IMAGES] = "folder_yellow16.png"


def _icon_for_type(node_type: NodeType) -> QIcon | None:
    _ensure_icons()
    fname = _NODE_ICON.get(node_type)
    if fname is None:
        return None
    path = os.path.join(_ICONS_DIR, fname)
    if os.path.isfile(path):
        return QIcon(path)
    return None


class NodeType(IntEnum):
    ROOT = auto()
    PROJECT = auto()
    DATASET = auto()
    IMAGE = auto()
    ORPHANED_DATASETS = auto()
    ORPHANED_IMAGES = auto()


# Custom data role to store metadata on items
WRAPPER_ROLE = Qt.ItemDataRole.UserRole + 1
NODE_TYPE_ROLE = Qt.ItemDataRole.UserRole + 2


class _FetchChildrenWorker(QThread):
    """Background worker that fetches children for an OmeroTreeItem."""

    finished = pyqtSignal(object, list)  # (parent_item, children_data)

    def __init__(self, conn, parent_item: OmeroTreeItem):
        super().__init__()
        self._conn = conn
        self._parent_item = parent_item

    def run(self):
        try:
            children = list(self._parent_item.yield_children(self._conn))
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to fetch children: %s", exc)
            children = []
        self.finished.emit(self._parent_item, children)


class OmeroTreeItem(QStandardItem):
    """A single node in the OMERO hierarchy tree.

    Parameters
    ----------
    label : str
        Display text.
    node_type : NodeType
        The kind of OMERO entity this item represents.
    omero_id : int | None
        The OMERO object id (or *None* for virtual containers).
    wrapper : object | None
        The underlying OMERO wrapper object (ImageWrapper, etc.).
    child_count : int | None
        Hint for child count (shown in parentheses, enables expand arrow).
    """

    def __init__(
        self,
        label: str,
        node_type: NodeType,
        omero_id: int | None = None,
        wrapper: Any = None,
        child_count: int | None = None,
    ):
        display = label
        if child_count is not None and child_count >= 0:
            display = f"{label} ({child_count})"
        super().__init__(display)

        self.node_type = node_type
        self.omero_id = omero_id
        self._wrapper = wrapper
        self._has_fetched = False

        self.setData(wrapper, WRAPPER_ROLE)
        self.setData(int(node_type), NODE_TYPE_ROLE)
        self.setEditable(False)

        icon = _icon_for_type(node_type)
        if icon is not None:
            self.setIcon(icon)

        # If we know there are children, add a placeholder so the expand
        # arrow is visible *before* we actually fetch them.
        if child_count and child_count > 0:
            self.appendRow(QStandardItem())  # placeholder

    # ------------------------------------------------------------------
    # Lazy child loading helpers
    # ------------------------------------------------------------------

    @property
    def has_fetched(self) -> bool:
        return self._has_fetched

    def mark_fetched(self) -> None:
        self._has_fetched = True

    def yield_children(self, conn):
        """Generator that yields ``(label, NodeType, id, wrapper, child_count)``
        tuples for each child.  Called from a background thread."""
        if self.node_type == NodeType.PROJECT:
            for ds in conn.getObjects(
                "Dataset",
                opts={"project": self.omero_id, "order_by": "obj.name"},
            ):
                n_img = ds.countChildren()
                yield (ds.getName(), NodeType.DATASET, ds.getId(), ds, n_img)

        elif self.node_type == NodeType.DATASET:
            for img in conn.getObjects(
                "Image",
                opts={"dataset": self.omero_id, "order_by": "obj.name"},
            ):
                yield (img.getName(), NodeType.IMAGE, img.getId(), img, None)

        elif self.node_type == NodeType.ORPHANED_DATASETS:
            for ds in conn.getObjects(
                "Dataset",
                opts={"orphaned": True, "order_by": "obj.name"},
            ):
                n_img = ds.countChildren()
                yield (ds.getName(), NodeType.DATASET, ds.getId(), ds, n_img)

        elif self.node_type == NodeType.ORPHANED_IMAGES:
            for img in conn.getObjects(
                "Image",
                opts={"orphaned": True, "order_by": "obj.name"},
            ):
                yield (img.getName(), NodeType.IMAGE, img.getId(), img, None)


class OmeroTreeModel(QStandardItemModel):
    """Lazy-loading model for the OMERO Project → Dataset → Image tree.

    Call :meth:`load_root` after the gateway is connected to populate the
    top level.
    """

    loading_started = pyqtSignal()
    loading_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._conn = None
        self._workers: list[_FetchChildrenWorker] = []
        self.setHorizontalHeaderLabels(["OMERO"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_root(self, conn, owner_id: int | None = None) -> None:
        """Populate the root level (projects + orphaned containers).

        Parameters
        ----------
        conn : BlitzGateway
            Connected gateway.
        owner_id : int | None
            If given, filter objects to this experimenter.
        """
        self._conn = conn
        self.clear()
        self.setHorizontalHeaderLabels(["OMERO"])
        self.loading_started.emit()

        opts: dict[str, Any] = {"order_by": "obj.name"}
        if owner_id is not None:
            opts["experimenter"] = owner_id

        # Projects
        for proj in conn.getObjects("Project", opts=opts):
            n_ds = proj.countChildren()
            item = OmeroTreeItem(
                proj.getName(), NodeType.PROJECT, proj.getId(), proj, n_ds
            )
            self.appendRow(item)

        # Orphaned datasets
        orphan_ds_opts = dict(opts, orphaned=True)
        orphan_ds = list(conn.getObjects("Dataset", opts=orphan_ds_opts))
        if orphan_ds:
            folder = OmeroTreeItem(
                "Orphaned Datasets",
                NodeType.ORPHANED_DATASETS,
                child_count=len(orphan_ds),
            )
            self.appendRow(folder)

        # Orphaned images
        orphan_img_opts = dict(opts, orphaned=True)
        orphan_imgs = list(conn.getObjects("Image", opts=orphan_img_opts))
        if orphan_imgs:
            folder = OmeroTreeItem(
                "Orphaned Images",
                NodeType.ORPHANED_IMAGES,
                child_count=len(orphan_imgs),
            )
            self.appendRow(folder)

        self.loading_finished.emit()

    def fetch_children(self, index: QModelIndex) -> None:
        """Fetch children for the node at *index* in a background thread."""
        if not index.isValid():
            return
        item = self.itemFromIndex(index)
        if not isinstance(item, OmeroTreeItem):
            return
        if item.has_fetched or self._conn is None:
            return

        item.mark_fetched()
        worker = _FetchChildrenWorker(self._conn, item)
        worker.finished.connect(self._on_children_fetched)
        self._workers.append(worker)
        worker.start()

    # ------------------------------------------------------------------
    def _on_children_fetched(self, parent_item: OmeroTreeItem, children: list) -> None:
        # Remove placeholder row(s)
        parent_item.removeRows(0, parent_item.rowCount())

        for label, ntype, oid, wrapper, child_count in children:
            child = OmeroTreeItem(label, ntype, oid, wrapper, child_count)
            parent_item.appendRow(child)

        # Clean up finished workers
        self._workers = [w for w in self._workers if w.isRunning()]

    # ------------------------------------------------------------------
    # canFetchMore / fetchMore — integrate with QTreeView
    # ------------------------------------------------------------------

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if not parent.isValid():
            return False
        item = self.itemFromIndex(parent)
        if isinstance(item, OmeroTreeItem):
            return not item.has_fetched and item.rowCount() > 0
        return False

    def fetchMore(self, parent: QModelIndex) -> None:
        self.fetch_children(parent)

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        if not parent.isValid():
            return self.rowCount() > 0
        item = self.itemFromIndex(parent)
        if isinstance(item, OmeroTreeItem):
            # If not yet fetched but has placeholder → show expand arrow
            if not item.has_fetched and item.rowCount() > 0:
                return True
            return item.rowCount() > 0
        return super().hasChildren(parent)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @staticmethod
    def get_wrapper(index: QModelIndex):
        """Return the OMERO wrapper object for *index*, or *None*."""
        if not index.isValid():
            return None
        return index.data(WRAPPER_ROLE)

    @staticmethod
    def get_node_type(index: QModelIndex) -> NodeType | None:
        val = index.data(NODE_TYPE_ROLE)
        if val is not None:
            return NodeType(val)
        return None
