"""
OmeroBrowserDialog — QuPath-style resizable dialog for browsing OMERO.

Layout (mirrors the QuPath screenshot):

    ┌─────────────────────────────────────────────────┐
    │  Server: host    Username: user                 │
    ├─────────────────┬───────────────────────────────┤
    │ Group ▼  Owner ▼│                               │
    │                 │  ┌────────────────────┐       │
    │  ▸ Project A    │  │   Thumbnail        │       │
    │  ▸ Project B    │  └────────────────────┘       │
    │  ▾ Test         │                               │
    │    ▸ dataset    │  Attribute │ Value             │
    │      image.tiff │  Name      │ image.tiff        │
    │                 │  Id        │ 29047             │
    │                 │  ...       │ ...               │
    │ Filter ________ │                               │
    ├─────────────────┴───────────────────────────────┤
    │                              [Import]           │
    └─────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QModelIndex, QSettings, QSize, QSortFilterProxyModel, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .gateway import OmeroGateway
from .selection_context import SelectedImageContext
from .tree_model import NODE_TYPE_ROLE, WRAPPER_ROLE, NodeType, OmeroTreeModel
from .widgets import ArrowComboBox

log = logging.getLogger(__name__)

_SETTINGS_GROUP_KEY = "omero_browser_qt/last_group_id"
_SETTINGS_OWNER_KEY = "omero_browser_qt/last_owner_id"
_SETTINGS_PATH_KEY = "omero_browser_qt/last_tree_path"


# ------------------------------------------------------------------
# Thumbnail loader (background thread)
# ------------------------------------------------------------------

class _ThumbnailWorker(QThread):
    """Fetches a thumbnail for an OMERO image in the background."""

    finished = pyqtSignal(int, QPixmap)  # (image_id, pixmap)

    def __init__(self, image_wrapper, size: int = 256):
        super().__init__()
        self._image = image_wrapper
        self._size = size

    def run(self):
        try:
            data = self._image.getThumbnail(size=(self._size, self._size))
            if data:
                qimg = QImage.fromData(data)
                pix = QPixmap.fromImage(qimg)
                self.finished.emit(self._image.getId(), pix)
                return
        except Exception as exc:  # noqa: BLE001
            log.debug("Thumbnail fetch failed: %s", exc)
        self.finished.emit(self._image.getId(), QPixmap())


# ------------------------------------------------------------------
# Filter proxy for name-based filtering
# ------------------------------------------------------------------

class _NameFilterProxy(QSortFilterProxyModel):
    """Recursive proxy that shows items whose display text matches."""

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        pattern = self.filterRegularExpression().pattern()
        if not pattern:
            return True
        idx = self.sourceModel().index(source_row, 0, source_parent)
        text = idx.data(Qt.ItemDataRole.DisplayRole) or ""
        if pattern.lower() in text.lower():
            return True
        # Accept parent if any child matches
        model = self.sourceModel()
        for r in range(model.rowCount(idx)):
            if self.filterAcceptsRow(r, idx):
                return True
        return False


# ------------------------------------------------------------------
# Main dialog
# ------------------------------------------------------------------

class OmeroBrowserDialog(QDialog):
    """Resizable dialog for browsing an OMERO server and selecting images.

    Usage::

        dlg = OmeroBrowserDialog(parent, gateway=my_gateway)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            images = dlg.get_selected_images()
            # images is a list of OMERO ImageWrapper objects

    Signals
    -------
    images_selected(list)
        Emitted with a list of OMERO ImageWrapper objects when the user
        clicks *Import* or double-clicks an image.
    """

    images_selected = pyqtSignal(list)
    LOGOUT_CODE = 1001

    def __init__(self, parent=None, *, gateway: OmeroGateway | None = None):
        super().__init__(parent)
        self.setWindowTitle("OMERO Browser")
        self.resize(920, 620)
        self.setMinimumSize(600, 400)

        self._gw = gateway or OmeroGateway()
        if not self._gw.is_connected():
            self._gw.try_restore_session()
        self._model = OmeroTreeModel(self)
        self._proxy = _NameFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setRecursiveFilteringEnabled(True)
        self._thumb_worker: _ThumbnailWorker | None = None

        self._build_ui()
        self._populate()

    # ==================================================================
    # UI construction
    # ==================================================================

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QDialog { background: #111315; color: #eceff1; }"
            "QLabel { color: #d5d9dd; }"
            "QComboBox, QLineEdit, QTreeView, QTableWidget {"
            "background: #1b1e21; color: #eceff1; border: 1px solid #41464b;"
            "border-radius: 6px; }"
            "QComboBox, QLineEdit { padding: 6px 8px; }"
            "QComboBox:focus, QLineEdit:focus, QTreeView:focus, QTableWidget:focus { border-color: #8d949b; }"
            "QComboBox { padding-right: 24px; }"
            "QComboBox::drop-down {"
            "subcontrol-origin: padding; subcontrol-position: top right;"
            "width: 26px; background: #25292d; border-left: 1px solid #41464b;"
            "border-top-right-radius: 6px; border-bottom-right-radius: 6px; }"
            "QTreeView { alternate-background-color: #171a1d; padding: 4px; }"
            "QTreeView::item:selected { background: #585e65; color: #f6f7f8; }"
            "QTreeView::item:hover { background: #2b3035; }"
            "QHeaderView::section {"
            "background: #262a2e; color: #eceff1; border: none; border-bottom: 1px solid #41464b;"
            "padding: 6px 8px; font-weight: 600; }"
            "QPushButton {"
            "background: #1e293b; color: #e2e8f0; border: 1px solid #334155;"
            "border-radius: 6px; padding: 6px 12px; font-weight: 600; }"
            "QPushButton:hover { background: #273449; border-color: #475569; }"
            "QPushButton:pressed { background: #0f172a; }"
            "QPushButton:disabled { background: #1a1d20; color: #727980; border-color: #30353a; }"
            "QScrollBar:vertical, QScrollBar:horizontal {"
            "background: #181b1e; border: none; margin: 0; }"
            "QScrollBar::handle:vertical, QScrollBar::handle:horizontal {"
            "background: #60666d; border-radius: 5px; min-height: 24px; min-width: 24px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { background: none; border: none; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # --- Title ---
        title = QLabel("OMERO")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #f3f4f6;")
        root.addWidget(title)
        root.addSpacing(2)

        # --- Top bar ---
        top = QHBoxLayout()
        self._server_label = QLabel()
        self._server_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        top.addWidget(QLabel("Server:"))
        top.addWidget(self._server_label)
        top.addSpacing(20)
        self._user_label = QLabel()
        self._user_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        top.addWidget(QLabel("Username:"))
        top.addWidget(self._user_label)
        top.addStretch()
        root.addLayout(top)

        # --- Splitter (left tree | right detail) ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # LEFT PANEL
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        # Group / Owner combos
        filter_row = QHBoxLayout()
        self._group_combo = ArrowComboBox()
        self._group_combo.setMinimumWidth(100)
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)
        filter_row.addWidget(self._group_combo)
        self._owner_combo = ArrowComboBox()
        self._owner_combo.setMinimumWidth(120)
        self._owner_combo.currentIndexChanged.connect(self._on_owner_changed)
        filter_row.addWidget(self._owner_combo)
        left_lay.addLayout(filter_row)

        # Tree view
        self._tree = QTreeView()
        self._tree.setModel(self._proxy)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(True)
        self._tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self._tree.selectionModel().selectionChanged.connect(self._on_selection)
        self._tree.doubleClicked.connect(self._on_double_click)
        self._tree.expanded.connect(self._on_expanded)
        left_lay.addWidget(self._tree, 1)

        # Filter line
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter images by name")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._on_filter_text)
        left_lay.addWidget(self._filter_edit)

        splitter.addWidget(left)

        # RIGHT PANEL
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 0, 0, 0)

        # Thumbnail
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(QSize(260, 260))
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet(
            "background: #1b1e21; border: 1px solid #41464b; border-radius: 8px;"
        )
        right_lay.addWidget(self._thumb_label, 0, Qt.AlignmentFlag.AlignHCenter)

        # Attributes table
        self._attr_table = QTableWidget(0, 2)
        self._attr_table.setHorizontalHeaderLabels(["Attribute", "Value"])
        self._attr_table.horizontalHeader().setStretchLastSection(True)
        self._attr_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._attr_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._attr_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._attr_table.verticalHeader().setVisible(False)
        self._placeholder = QLabel("Select an image to view details")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #8f969d;")
        right_lay.addWidget(self._placeholder)
        right_lay.addWidget(self._attr_table, 1)
        self._attr_table.hide()

        splitter.addWidget(right)
        splitter.setSizes([360, 540])
        root.addWidget(splitter, 1)

        # --- Bottom bar ---
        bottom = QHBoxLayout()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setMinimumWidth(100)
        self._cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(self._cancel_btn)
        self._logout_btn = QPushButton("Logout")
        self._logout_btn.setMinimumWidth(100)
        self._logout_btn.clicked.connect(self._on_logout)
        bottom.addWidget(self._logout_btn)
        bottom.addStretch()
        self._import_btn = QPushButton("Import")
        self._import_btn.setEnabled(False)
        self._import_btn.setMinimumWidth(100)
        self._import_btn.clicked.connect(self._on_import)
        bottom.addWidget(self._import_btn)
        root.addLayout(bottom)

    # ==================================================================
    # Populate combos / tree from gateway
    # ==================================================================

    def _populate(self) -> None:
        conn = self._gw.get_connection()
        if conn is None:
            return

        self._server_label.setText(self._gw.host)
        self._user_label.setText(self._gw.username)

        # Groups
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        groups = self._gw.get_groups()
        for gid, gname in groups:
            self._group_combo.addItem(gname, gid)
        # Select the user's current group (or last-used group)
        settings = QSettings("omero_browser_qt", "omero_browser_qt")
        last_gid = settings.value(_SETTINGS_GROUP_KEY, None)
        selected = False
        if last_gid is not None:
            last_gid = int(last_gid)
            for i in range(self._group_combo.count()):
                if self._group_combo.itemData(i) == last_gid:
                    self._group_combo.setCurrentIndex(i)
                    selected = True
                    break
        if not selected:
            cur_gid = conn.getGroupFromContext().getId()
            for i in range(self._group_combo.count()):
                if self._group_combo.itemData(i) == cur_gid:
                    self._group_combo.setCurrentIndex(i)
                    break
        self._group_combo.blockSignals(False)

        self._refresh_owners()
        self._refresh_tree()
        self._restore_tree_path()

    def _refresh_owners(self) -> None:
        self._owner_combo.blockSignals(True)
        self._owner_combo.clear()
        self._owner_combo.addItem("All members", None)
        gid = self._group_combo.currentData()
        if gid is not None:
            for eid, ename in self._gw.get_experimenters_in_group(gid):
                self._owner_combo.addItem(ename, eid)
        # Pre-select last-used owner, fall back to current user
        settings = QSettings("omero_browser_qt", "omero_browser_qt")
        last_oid = settings.value(_SETTINGS_OWNER_KEY, None)
        selected = False
        if last_oid is not None:
            if last_oid == "":
                # "All members" was selected
                self._owner_combo.setCurrentIndex(0)
                selected = True
            else:
                last_oid = int(last_oid)
                for i in range(self._owner_combo.count()):
                    if self._owner_combo.itemData(i) == last_oid:
                        self._owner_combo.setCurrentIndex(i)
                        selected = True
                        break
        if not selected:
            conn = self._gw.get_connection()
            if conn:
                me = conn.getUserId()
                for i in range(self._owner_combo.count()):
                    if self._owner_combo.itemData(i) == me:
                        self._owner_combo.setCurrentIndex(i)
                        break
        self._owner_combo.blockSignals(False)

    def _refresh_tree(self) -> None:
        conn = self._gw.get_connection()
        if conn is None:
            return
        gid = self._group_combo.currentData()
        if gid is not None:
            self._gw.set_group(gid)
        owner_id = self._owner_combo.currentData()
        self._model.load_root(conn, owner_id=owner_id)

    # ==================================================================
    # Slots
    # ==================================================================

    def _on_group_changed(self, _idx: int) -> None:
        self._refresh_owners()
        self._refresh_tree()

    def _on_owner_changed(self, _idx: int) -> None:
        self._refresh_tree()

    def _on_expanded(self, proxy_index: QModelIndex) -> None:
        src_index = self._proxy.mapToSource(proxy_index)
        self._model.fetch_children(src_index)

    def _on_filter_text(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)

    def _on_selection(self) -> None:
        wrappers = self._selected_image_wrappers()
        self._import_btn.setEnabled(len(wrappers) > 0)

        if len(wrappers) == 1:
            self._show_image_detail(wrappers[0])
        elif len(wrappers) > 1:
            self._show_placeholder(f"{len(wrappers)} images selected")
        else:
            self._show_placeholder("Select an image to view details")

    def _on_double_click(self, proxy_index: QModelIndex) -> None:
        src = self._proxy.mapToSource(proxy_index)
        ntype = OmeroTreeModel.get_node_type(src)
        if ntype == NodeType.IMAGE:
            self._on_import()

    def _on_import(self) -> None:
        imgs = self._selected_image_wrappers()
        if imgs:
            self._save_state()
            self.images_selected.emit(imgs)
            self.accept()

    def _on_logout(self) -> None:
        self._gw.disconnect()
        self.done(self.LOGOUT_CODE)

    # ==================================================================
    # State persistence (group, owner, tree path)
    # ==================================================================

    def _save_state(self) -> None:
        """Save current group, owner, and selected tree path to QSettings."""
        settings = QSettings("omero_browser_qt", "omero_browser_qt")

        gid = self._group_combo.currentData()
        if gid is not None:
            settings.setValue(_SETTINGS_GROUP_KEY, int(gid))

        oid = self._owner_combo.currentData()
        settings.setValue(_SETTINGS_OWNER_KEY, "" if oid is None else int(oid))

        # Save the tree path as a list of OMERO object ids
        path = self._get_selected_tree_path()
        settings.setValue(_SETTINGS_PATH_KEY, path)

    def _get_selected_tree_path(self) -> list[int]:
        """Return the OMERO ids from root to the selected item."""
        indexes = self._tree.selectionModel().selectedIndexes()
        if not indexes:
            return []
        src_idx = self._proxy.mapToSource(indexes[0])
        path = []
        item = self._model.itemFromIndex(src_idx)
        while item is not None:
            from .tree_model import OmeroTreeItem
            if isinstance(item, OmeroTreeItem) and item.omero_id is not None:
                path.append(item.omero_id)
            item = item.parent()
        path.reverse()
        return path

    def _restore_tree_path(self) -> None:
        """Expand and select the previously saved tree path."""
        settings = QSettings("omero_browser_qt", "omero_browser_qt")
        path = settings.value(_SETTINGS_PATH_KEY, [])
        if not path:
            return
        if isinstance(path, str):
            path = [path]
        path = [int(p) for p in path]

        from .tree_model import OmeroTreeItem

        parent_idx = QModelIndex()
        for target_id in path:
            found = False
            for row in range(self._model.rowCount(parent_idx)):
                idx = self._model.index(row, 0, parent_idx)
                item = self._model.itemFromIndex(idx)
                if isinstance(item, OmeroTreeItem) and item.omero_id == target_id:
                    # Expand and fetch children synchronously
                    proxy_idx = self._proxy.mapFromSource(idx)
                    self._tree.expand(proxy_idx)
                    if not item.has_fetched:
                        self._model.fetch_children(idx)
                    parent_idx = idx
                    found = True
                    break
            if not found:
                break
        else:
            # Select the last matched item
            proxy_idx = self._proxy.mapFromSource(parent_idx)
            self._tree.selectionModel().select(
                proxy_idx,
                self._tree.selectionModel().SelectionFlag.ClearAndSelect,
            )
            self._tree.scrollTo(proxy_idx)

    # ==================================================================
    # Detail panel
    # ==================================================================

    def _show_placeholder(self, text: str) -> None:
        self._attr_table.hide()
        self._placeholder.setText(text)
        self._placeholder.show()
        self._thumb_label.clear()

    def _show_image_detail(self, image) -> None:
        self._placeholder.hide()
        self._attr_table.show()
        self._populate_attributes(image)
        self._load_thumbnail(image)

    def _populate_attributes(self, image) -> None:
        from .image_loader import get_image_metadata

        meta = get_image_metadata(image)
        rows: list[tuple[str, str]] = [
            ("Name", meta["name"]),
            ("Id", str(meta["id"])),
        ]
        # Owner / group
        try:
            rows.append(("Owner", image.getOwnerFullName()))
        except Exception:  # noqa: BLE001
            pass
        try:
            rows.append(("Group", image.getGroupName()))
        except Exception:  # noqa: BLE001
            pass
        # Acquisition date
        try:
            acq = image.getAcquisitionDate()
            if acq:
                rows.append(("Acquisition date", str(acq)))
        except Exception:  # noqa: BLE001
            pass

        rows.append(("Image width", f"{meta['size_x']} px"))
        rows.append(("Image height", f"{meta['size_y']} px"))

        # Uncompressed size
        import numpy as np
        bpp = np.dtype(meta["pixel_type"]).itemsize
        total = meta["size_x"] * meta["size_y"] * meta["size_z"] * meta["size_c"] * meta["size_t"] * bpp
        if total > 1024**3:
            rows.append(("Uncompressed size", f"{total / 1024**3:.1f} GB"))
        else:
            rows.append(("Uncompressed size", f"{total / 1024**2:.1f} MB"))

        rows.append(("Nb of z-slices", str(meta["size_z"])))
        rows.append(("Nb of channels", str(meta["size_c"])))
        rows.append(("Nb of timepoints", str(meta["size_t"])))

        if meta["pixel_size_x"]:
            rows.append(("Pixel size X", f"{meta['pixel_size_x']} µm"))
        if meta["pixel_size_y"]:
            rows.append(("Pixel size Y", f"{meta['pixel_size_y']} µm"))
        if meta["pixel_size_z"]:
            rows.append(("Pixel size Z", f"{meta['pixel_size_z']} µm"))
        rows.append(("Pixel type", meta["pixel_type"]))

        self._attr_table.setRowCount(len(rows))
        for i, (attr, val) in enumerate(rows):
            a_item = QTableWidgetItem(attr)
            a_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            v_item = QTableWidgetItem(val)
            v_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            v_item.setToolTip(val)
            self._attr_table.setItem(i, 0, a_item)
            self._attr_table.setItem(i, 1, v_item)

    def _load_thumbnail(self, image) -> None:
        self._thumb_label.setText("Loading…")
        worker = _ThumbnailWorker(image, size=256)
        worker.finished.connect(self._on_thumbnail)
        self._thumb_worker = worker
        worker.start()

    def _on_thumbnail(self, image_id: int, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            self._thumb_label.setText("No preview")
            return
        scaled = pixmap.scaled(
            self._thumb_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumb_label.setPixmap(scaled)

    # ==================================================================
    # Public API
    # ==================================================================

    def get_selected_images(self) -> list:
        """Return the list of selected OMERO ImageWrapper objects."""
        return self._selected_image_wrappers()

    def get_selected_image_contexts(self) -> list[SelectedImageContext]:
        """Return structured selection data for the selected images."""
        contexts: list[SelectedImageContext] = []
        for idx in self._tree.selectionModel().selectedIndexes():
            src = self._proxy.mapToSource(idx)
            if OmeroTreeModel.get_node_type(src) != NodeType.IMAGE:
                continue
            ctx = self._context_from_source_index(src)
            if ctx is not None:
                contexts.append(ctx)
        return contexts

    @classmethod
    def select_images(
        cls,
        parent=None,
        *,
        gateway: OmeroGateway | None = None,
    ) -> list:
        """Show login if needed, then browse and return selected images.

        Returns an empty list when the user cancels or closes the dialogs.
        If the user logs out from the browser, the login dialog is shown
        again and the flow continues.
        """
        from .login_dialog import LoginDialog

        gw = gateway or OmeroGateway()

        while True:
            if not gw.is_connected():
                gw.try_restore_session()
            if not gw.is_connected():
                dlg = LoginDialog(parent, gateway=gw)
                if dlg.exec() != LoginDialog.DialogCode.Accepted:
                    return []

            browser = cls(parent, gateway=gw)
            result = browser.exec()
            if result == cls.LOGOUT_CODE:
                continue
            if result != cls.DialogCode.Accepted:
                return []
            return browser.get_selected_images()

    @classmethod
    def select_image_contexts(
        cls,
        parent=None,
        *,
        gateway: OmeroGateway | None = None,
    ) -> list[SelectedImageContext]:
        """Show login if needed, then browse and return selected contexts."""
        from .login_dialog import LoginDialog

        gw = gateway or OmeroGateway()

        while True:
            if not gw.is_connected():
                gw.try_restore_session()
            if not gw.is_connected():
                dlg = LoginDialog(parent, gateway=gw)
                if dlg.exec() != LoginDialog.DialogCode.Accepted:
                    return []

            browser = cls(parent, gateway=gw)
            result = browser.exec()
            if result == cls.LOGOUT_CODE:
                continue
            if result != cls.DialogCode.Accepted:
                return []
            return browser.get_selected_image_contexts()

    def _selected_image_wrappers(self) -> list:
        """Collect image wrappers from current tree selection."""
        wrappers = []
        for idx in self._tree.selectionModel().selectedIndexes():
            src = self._proxy.mapToSource(idx)
            ntype = OmeroTreeModel.get_node_type(src)
            if ntype == NodeType.IMAGE:
                w = OmeroTreeModel.get_wrapper(src)
                if w is not None:
                    wrappers.append(w)
        return wrappers

    def _context_from_source_index(self, src_idx: QModelIndex) -> SelectedImageContext | None:
        item = self._model.itemFromIndex(src_idx)
        if item is None:
            return None
        wrapper = OmeroTreeModel.get_wrapper(src_idx)
        if wrapper is None:
            return None

        project_id = None
        project_name = None
        dataset_id = None
        dataset_name = None
        path_labels: list[str] = []

        from .tree_model import OmeroTreeItem

        cursor = item
        while isinstance(cursor, OmeroTreeItem):
            label = cursor.text()
            if " (" in label:
                label = label.rsplit(" (", 1)[0]
            if cursor.node_type not in {NodeType.ORPHANED_IMAGES, NodeType.ORPHANED_DATASETS}:
                path_labels.append(label)
            if cursor.node_type == NodeType.PROJECT and project_id is None:
                project_id = cursor.omero_id
                project_name = label
            elif cursor.node_type == NodeType.DATASET and dataset_id is None:
                dataset_id = cursor.omero_id
                dataset_name = label
            cursor = cursor.parent()

        path_labels.reverse()

        return SelectedImageContext(
            image=wrapper,
            image_id=getattr(wrapper, "getId", lambda: None)(),
            image_name=getattr(wrapper, "getName", lambda: "")(),
            group_id=self._group_combo.currentData(),
            group_name=self._group_combo.currentText() or None,
            owner_id=self._owner_combo.currentData(),
            owner_name=self._owner_combo.currentText() or None,
            project_id=project_id,
            project_name=project_name,
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            path_labels=tuple(path_labels),
            backend="ICE",
        )
