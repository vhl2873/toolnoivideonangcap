from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from utils.machine_unlock import complete_setup, verify_password


class SetupUnlockDialog(QDialog):
    """Hộp thoại nhập mật khẩu lần đầu trên máy này."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Thiết lập bảo mật")
        self.setModal(True)
        self.setMinimumWidth(360)

        info = QLabel(
            "Đây là lần chạy đầu trên máy này.\n"
            "Nhập mật khẩu để kích hoạt, sau đó không cần nhập lại trên cùng máy."
        )
        info.setWordWrap(True)

        self._password = QLineEdit()


        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("Mật khẩu")
        self._password.returnPressed.connect(self._try_accept)

        form = QFormLayout()
        form.addRow("Mật khẩu:", self._password)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self._password.setFocus(Qt.FocusReason.PopupFocusReason)

    def _try_accept(self) -> None:
        if verify_password(self._password.text()):
            complete_setup()
            self.accept()
            return
        QMessageBox.warning(
            self,
            "Sai mật khẩu",
            "Mật khẩu không đúng. Hãy thử lại hoặc Thoát.",
        )
        self._password.clear()
        self._password.setFocus()
