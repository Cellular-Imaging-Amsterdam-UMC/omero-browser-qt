"""
LoginDialog — PyQt6 login dialog for OMERO servers.

Only the server hostname is persisted directly; credentials are never
stored. A temporary OMERO session UUID may be cached separately by the
gateway for short-lived re-login across app restarts.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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
from .widgets import ArrowComboBox


class LoginDialog(QDialog):
    """Modal dialog that collects OMERO credentials and connects.

    On ``accept()`` the :class:`OmeroGateway` singleton is connected and
    ready to use.  The dialog pre-fills the server combo with previously
    used hostnames, restores runtime-only username/password values while
    the app is open, and requests short-lived OMERO session reuse across
    restarts. The password itself is not persisted to disk.

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
        self.setStyleSheet(
            "QDialog { background: #111315; color: #eceff1; }"
            "QLabel { color: #d5d9dd; }"
            "QLineEdit, QComboBox, QSpinBox {"
            "background: #1d2023; color: #eceff1; border: 1px solid #43484d;"
            "border-radius: 6px; padding: 6px 8px; }"
            "QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #8d949b; }"
            "QComboBox { padding-right: 24px; }"
            "QComboBox::drop-down {"
            "subcontrol-origin: padding; subcontrol-position: top right;"
            "width: 26px; background: #25292d; border-left: 1px solid #43484d;"
            "border-top-right-radius: 6px; border-bottom-right-radius: 6px; }"
            "QSpinBox { padding-right: 44px; }"
            "QSpinBox::up-button, QSpinBox::down-button {"
            "width: 20px; background: #25292d; border-left: 1px solid #43484d; }"
            "QSpinBox::up-button {"
            "subcontrol-origin: padding; subcontrol-position: top right;"
            "border-top-right-radius: 6px; }"
            "QSpinBox::down-button {"
            "subcontrol-origin: padding; subcontrol-position: bottom right;"
            "border-bottom-right-radius: 6px; border-top: 1px solid #34393d; }"
            "QPushButton {"
            "background: #1e293b; color: #e2e8f0; border: 1px solid #334155;"
            "border-radius: 6px; padding: 6px 12px; font-weight: 600; }"
            "QPushButton:hover { background: #273449; border-color: #475569; }"
            "QPushButton:pressed { background: #0f172a; }"
            "QPushButton:disabled { background: #1a1d20; color: #727980; border-color: #30353a; }"
            "QProgressBar { background: #1a1d20; border: 1px solid #34393d; border-radius: 3px; }"
            "QProgressBar::chunk { background: #aeb4ba; border-radius: 3px; }"
        )
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("OMERO")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #f3f4f6;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        runtime_fields = self._gw.runtime_login_fields()

        # Server combo (editable — user can type a new host)
        self._server_combo = ArrowComboBox()
        self._server_combo.setEditable(True)
        self._server_combo.setMinimumWidth(300)
        self._server_combo.addItems(OmeroGateway.saved_servers())
        self._server_combo.setCurrentText(str(runtime_fields.get("host", "")))
        if not self._server_combo.currentText() and self._server_combo.count() > 0:
            self._server_combo.setCurrentIndex(0)
        form.addRow("Server:", self._server_combo)

        # Port
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(int(runtime_fields.get("port", 4064)))
        form.addRow("Port:", self._port_spin)

        # Username
        self._user_edit = QLineEdit()
        self._user_edit.setPlaceholderText("username")
        self._user_edit.setText(str(runtime_fields.get("username", "")))
        form.addRow("Username:", self._user_edit)

        # Password
        self._pass_edit = QLineEdit()
        self._pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._pass_edit.setPlaceholderText("password")
        self._pass_edit.setText(str(runtime_fields.get("password", "")))
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

        ok = self._gw.connect(
            host,
            port,
            user,
            pwd,
            remember_session=True,
        )

        self._progress.hide()
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

        if ok:
            self.accept()
        else:
            self._show_error("Connection failed. Check host/credentials and try again.")

    def _show_error(self, text: str) -> None:
        self._error_label.setText(text)
        self._error_label.show()
