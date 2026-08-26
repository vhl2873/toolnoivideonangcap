import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from ui.dashboard_window import DashboardWindow
from ui.styles import build_app_stylesheet
from utils.resources import resource_path


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Fast Video Studio")
    app.setOrganizationName("Local Tools")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(build_app_stylesheet())
    app_icon = QIcon(resource_path("assets", "app_icon.ico"))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = DashboardWindow()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.resize(1600, 940)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
