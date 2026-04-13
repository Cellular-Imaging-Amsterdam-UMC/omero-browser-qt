"""
LoginDialog — PyQt6 login dialog for OMERO servers.

Only the server hostname is persisted (via OmeroGateway.saved_servers);
credentials are never stored.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
)

from .gateway import OmeroGateway


class LoginDialog(QDialog):
    """Modal dialog that collects OMERO credentials and connects.

    On ``accept()`` the :class:`OmeroGateway` singleton is connected and
    ready to use.  The dialog pre-fills the server combo with previously
    used hostnames (no credentials are stored).

    Parameters
    ----------
    parent : QWidget | None
        Parent widget.
    gateway : OmeroGateway | None
        Gateway instance.  If *None* the singleton is used.
    """

    def __init__(self, parent=None, *, gateway: OmeroGateway | None = None):
        super().__init__(parent)
        self.setWindowTitle("OMERO Login")
        self.setMinimumWidth(380)
        self._gw = gateway or OmeroGateway()
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("OMERO")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #2196F3;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # Server combo (editable — user can type a new host)
        self._server_combo = QComboBox()
        self._server_combo.setEditable(True)
        self._server_combo.setMinimumWidth(300)
        self._server_combo.addItems(OmeroGateway.saved_servers())
        self._server_combo.setCurrentText("")
        if self._server_combo.count() > 0:
            self._server_combo.setCurrentIndex(0)
        form.addRow("Server:", self._server_combo)

        # Port
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(4064)
        form.addRow("Port:", self._port_spin)

        # Username
        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("username")
        form.addRow("Username:", self._user_edit)

        # Password
        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_edit.setPlaceholderText("password")
        form.addRow("Password:", self._pass_edit)

        layout.addLayout(form)

        # Progress bar (hidden until connecting)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.hide()
        layout.addWidget(self._progress)

        # Error label
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #d32f2f;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        # Buttons
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_login)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # Enter in password field triggers login
        self._pass_edit.returnPressed.connect(self._on_login)

    # ------------------------------------------------------------------
    # Login logic
    # ------------------------------------------------------------------

    def _on_login(self) -> None:
        host = self._server_combo.currentText().strip()
        port = self._port_spin.value()
        user = self._user_edit.text().strip()
        pwd = self._pass_edit.text()

        if not host or not user or not pwd:
            self._show_error("Please fill in all fields.")
            return

        self._error_label.hide()
        self._progress.show()
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        # Force UI repaint before blocking call
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        ok = self._gw.connect(host, port, user, pwd)

        self._progress.hide()
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

        if ok:
            self.accept()
        else:
            self._show_error("Connection failed. Check host/credentials and try again.")

    def _show_error(self, text: str) -> None:
        self._error_label.setText(text)
        self._error_label.show()
