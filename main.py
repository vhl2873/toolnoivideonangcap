import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from ui.dashboard_window import DashboardWindow
from ui.setup_unlock_dialog import SetupUnlockDialog
from ui.styles import build_app_stylesheet
from utils.machine_unlock import is_setup_complete
from utils.resources import resource_path


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Fast Video Concatenator")
    app.setOrganizationName("Local Tools")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(build_app_stylesheet())
    app_icon = QIcon(resource_path("assets", "app_icon.ico"))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    if not is_setup_complete():
        unlock = SetupUnlockDialog()
        if not app_icon.isNull():
            unlock.setWindowIcon(app_icon)
        if unlock.exec() != SetupUnlockDialog.DialogCode.Accepted:
            return 0

    window = DashboardWindow()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.resize(1480, 880)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
