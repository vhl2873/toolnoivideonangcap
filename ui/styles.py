from __future__ import annotations


def build_app_stylesheet() -> str:
    return """
    * {
        font-family: "Segoe UI";
        font-size: 10pt;
        color: #f3f5f8;
        selection-background-color: #2dd4bf;
        selection-color: #071113;
    }

    QMainWindow,
    QWidget#appRoot {
        background: #0b0d12;
    }

    /* ── Dialog / popup windows ────────────────────────────────────────── */
    QDialog {
        background: #11151f;
    }
    QDialog QLabel {
        color: #e7ecf3;
    }
    QDialogButtonBox QPushButton {
        min-width: 80px;
    }

    /* ── Context menu (QMenu) ───────────────────────────────────────────── */
    QMenu {
        background: #111722;
        border: 1px solid #283246;
        border-radius: 8px;
        padding: 4px;
        color: #e7ecf3;
    }
    QMenu::item {
        padding: 8px 20px 8px 14px;
        border-radius: 5px;
        color: #e7ecf3;
    }
    QMenu::item:selected {
        background: #1e3a4a;
        color: #2dd4bf;
    }
    QMenu::item:disabled {
        color: #586274;
    }
    QMenu::separator {
        height: 1px;
        background: #202838;
        margin: 4px 10px;
    }

    /* ── ComboBox popup list ────────────────────────────────────────────── */
    QComboBox QAbstractItemView {
        background: #111722;
        border: 1px solid #283246;
        border-radius: 6px;
        color: #eef2f7;
        outline: 0;
        padding: 4px;
        selection-background-color: #2dd4bf;
        selection-color: #071113;
    }
    QComboBox QAbstractItemView::item {
        padding: 6px 12px;
        min-height: 28px;
    }
    QComboBox QAbstractItemView::item:hover {
        background: #1b2535;
    }

    QLabel#appTitle {
        color: #ffffff;
        font-size: 20pt;
        font-weight: 700;
        letter-spacing: 0;
    }

    QLabel#appSubtitle {
        color: #9aa4b2;
        font-size: 10pt;
    }

    QLabel[role="sectionTitle"] {
        color: #f7f8fb;
        font-size: 11pt;
        font-weight: 650;
    }

    QLabel[role="mutedText"] {
        color: #9aa4b2;
    }

    QLabel#statusLabel {
        background: #111722;
        border: 1px solid #253044;
        border-radius: 8px;
        color: #dbeafe;
        padding: 10px 12px;
        font-weight: 600;
    }

    QFrame#headerBar {
        background: #10131b;
        border: 1px solid #202838;
        border-radius: 8px;
    }

    QFrame[role="panel"] {
        background: #11151f;
        border: 1px solid #202838;
        border-radius: 8px;
    }

    QSplitter::handle {
        background: #0b0d12;
        height: 8px;
    }

    QListWidget,
    QPlainTextEdit,
    QLineEdit,
    QComboBox {
        background: #0f131c;
        border: 1px solid #283246;
        border-radius: 8px;
        color: #eef2f7;
        padding: 8px;
    }

    QListWidget {
        alternate-background-color: #141a25;
        outline: 0;
        padding: 6px;
    }

    QListWidget::item {
        border-radius: 6px;
        min-height: 82px;
        padding: 8px 10px;
        color: #e7ecf3;
    }

    QListWidget::item:hover {
        background: #1b2535;
    }

    QListWidget::item:selected {
        background: #2dd4bf;
        color: #071113;
    }

    QListWidget#fileStrip {
        alternate-background-color: transparent;
        border: 1px solid #283246;
    }

    QListWidget#fileStrip::viewport {
        background-color: #0f131c;
    }

    QListWidget#fileStrip QAbstractScrollArea::corner {
        background-color: #0f131c;
        border: none;
    }

    QListWidget#fileStrip QScrollBar:horizontal {
        background-color: #0f131c;
        height: 11px;
        margin: 0;
        border: none;
        border-radius: 5px;
    }

    QListWidget#fileStrip QScrollBar::handle:horizontal {
        background-color: #334155;
        border-radius: 5px;
        min-width: 36px;
    }

    QListWidget#fileStrip QScrollBar::handle:horizontal:hover {
        background-color: #475569;
    }

    QListWidget#fileStrip QScrollBar::add-line:horizontal,
    QListWidget#fileStrip QScrollBar::sub-line:horizontal {
        width: 0;
        height: 0;
    }

    QListWidget#fileStrip QScrollBar:vertical {
        width: 0px;
        margin: 0;
    }

    QListWidget#fileStrip::item {
        border-radius: 10px;
        min-width: 176px;
        max-width: 196px;
        min-height: 132px;
        padding: 8px 8px 10px 8px;
        color: #e7ecf3;
    }

    QListWidget#fileStrip::item:hover {
        background: #1b2535;
    }

    QListWidget#fileStrip::item:selected {
        background: #1e3a4a;
        color: #e0f2fe;
        border: 1px solid #2dd4bf;
    }

    QListWidget#streamSidebar {
        padding: 4px;
    }

    QListWidget#streamSidebar::item {
        min-height: 52px;
        padding: 8px 10px;
        white-space: normal;
    }

    QListWidget#streamSidebar::item:selected {
        background: #1e3a4a;
        color: #e0f2fe;
        border: 1px solid #2dd4bf;
    }

    QPlainTextEdit {
        color: #cbd5e1;
        font-family: "Cascadia Mono", "Consolas", "Segoe UI";
        font-size: 9.5pt;
        line-height: 140%;
    }

    QLineEdit {
        min-height: 20px;
    }

    QLineEdit:focus,
    QComboBox:focus,
    QListWidget:focus,
    QPlainTextEdit:focus {
        border: 1px solid #2dd4bf;
    }

    QComboBox {
        min-height: 20px;
        padding: 7px 30px 7px 10px;
        selection-background-color: #2dd4bf;
        selection-color: #071113;
    }

    QComboBox:hover {
        background: #121a27;
        border-color: #475569;
    }

    QComboBox:disabled {
        background: #151922;
        border-color: #202838;
        color: #586274;
    }

    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 26px;
        border-left: 1px solid #283246;
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
        background: #111827;
    }

    QComboBox::drop-down:hover {
        background: #1a2230;
    }

    QComboBox QAbstractItemView {
        background: #0f131c;
        border: 1px solid #283246;
        color: #eef2f7;
        outline: 0;
        padding: 4px;
        selection-background-color: #2dd4bf;
        selection-color: #071113;
    }

    QPushButton {
        background: #1a2230;
        border: 1px solid #334155;
        border-radius: 7px;
        color: #f8fafc;
        font-weight: 600;
        min-height: 30px;
        padding: 7px 14px;
    }

    QPushButton:hover {
        background: #253247;
        border-color: #475569;
    }

    QPushButton:pressed {
        background: #111827;
        border-color: #2dd4bf;
    }

    QPushButton:disabled {
        background: #151922;
        border-color: #202838;
        color: #586274;
    }

    QPushButton[variant="primary"] {
        background: #2dd4bf;
        border-color: #2dd4bf;
        color: #041011;
        font-weight: 750;
    }

    QPushButton[variant="primary"]:hover {
        background: #5eead4;
        border-color: #5eead4;
    }

    QPushButton[variant="danger"] {
        background: #2a1720;
        border-color: #7f1d1d;
        color: #fecaca;
    }

    QPushButton[variant="danger"]:hover {
        background: #3a1b26;
        border-color: #ef4444;
    }

    QPushButton[variant="quiet"] {
        background: transparent;
        border-color: #334155;
        color: #cbd5e1;
    }

    QPushButton[variant="quiet"]:hover {
        background: #1a2230;
    }

    QScrollBar:vertical {
        background: #0f131c;
        width: 12px;
        margin: 2px;
        border-radius: 6px;
    }

    QScrollBar::handle:vertical {
        background: #334155;
        border-radius: 6px;
        min-height: 40px;
    }

    QScrollBar::handle:vertical:hover {
        background: #475569;
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0;
    }

    QFileDialog,
    QMessageBox {
        background: #11151f;
    }
    """
