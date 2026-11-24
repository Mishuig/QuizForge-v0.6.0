"""
SecureTest Admin — головне вікно з вкладками.
Крок 8: Тести, Студенти, Результати + пароль адмінки.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase, QFont,  QPixmap
from PyQt6.QtWidgets import (
    QPushButton,
    QStackedWidget,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config_loader import load_config
from core.ui_theme import ASSETS, apply_theme, logo_pixmap
from core.db import get_db
from core.admin_auth import is_password_set, set_password, verify_password
from admin.tests_tab import TestsTab
from admin.students_tab import StudentsTab
from admin.results_tab import ResultsTab

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("admin")


class PasswordDialog(QDialog):
    """Встановлення або введення пароля адміна."""

    def __init__(self, mode: str = "login", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = mode  # "login" | "setup"
        self.setWindowTitle("QuizForge — Адмін")
        try:
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(ASSETS / "logo-admin.ico")))
        except Exception:
            pass
        self.setMinimumWidth(400)
        self.setMinimumHeight(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(20)

        layout.addStretch(1)

        # Логотип
        logo_layout = QHBoxLayout()
        logo_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_layout.setSpacing(12)
        logo_label = QLabel()
        try:
            pixmap = logo_pixmap("logo-admin.svg", 64)
            if not pixmap.isNull():
                logo_label.setPixmap(pixmap)
        except Exception:
            pass
        logo_layout.addWidget(logo_label)
        name_label = QLabel("QuizForge")
        name_label.setObjectName("logoLabel")
        logo_layout.addWidget(name_label)
        layout.addLayout(logo_layout)

        if mode == "setup":
            label = QLabel(
                "Перший запуск.\nВстановіть пароль адміністратора:"
            )
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        else:
            label = QLabel("Введіть пароль адміністратора:")
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)

        form = QFormLayout()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.returnPressed.connect(self.accept)
        form.addRow("Пароль:", self.pass_edit)

        if mode == "setup":
            self.pass2_edit = QLineEdit()
            self.pass2_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.pass2_edit.returnPressed.connect(self.accept)
            form.addRow("Ще раз:", self.pass2_edit)
        else:
            self.pass2_edit = None

        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Увійти")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Скасувати")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        layout.addStretch(1)

    def _validate(self) -> None:
        p1 = self.pass_edit.text()
        if self.mode == "setup":
            p2 = self.pass2_edit.text() if self.pass2_edit else ""
            if len(p1) < 4:
                QMessageBox.warning(self, "Пароль", "Мінімум 4 символи.")
                return
            if p1 != p2:
                QMessageBox.warning(self, "Пароль", "Паролі не збігаються.")
                return
        elif not p1:
            QMessageBox.warning(self, "Пароль", "Введіть пароль.")
            return
        self.accept()

    def password(self) -> str:
        return self.pass_edit.text()


class MainWindow(QMainWindow):
    def __init__(self, db, config: dict, org_name: str = "SecureTest") -> None:
        super().__init__()
        self.db = db
        self.config = config
        self.setWindowTitle("QuizForge — Адміністрування")
        self.setMinimumSize(900, 600)
        self.resize(1280, 800)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Верхня панель: логотип + вкладки
        topbar = QWidget()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(56)
        bar = QHBoxLayout(topbar)
        bar.setContentsMargins(24, 0, 24, 0)
        bar.setSpacing(0)

        logo = QLabel()
        pix = logo_pixmap("logo-admin.svg", 28)
        if not pix.isNull():
            logo.setPixmap(pix)
        bar.addWidget(logo)
        bar.addSpacing(10)
        title = QLabel("QuizForge")
        title.setObjectName("logoLabel")
        bar.addWidget(title)
        bar.addSpacing(28)

        self.btn_tests = QPushButton("Тести")
        self.btn_users = QPushButton("Користувачі")
        self.btn_results = QPushButton
        self.btn_theme = QPushButton("Тема")
        self.btn_theme.setObjectName("navTab")
        self.btn_theme.clicked.connect(self._toggle_theme)
("Результати")
        for b in (self.btn_tests, self.btn_users, self.btn_results):
            b.setObjectName("navTab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bar.addWidget(b)
        bar.addStretch(1)
        root.addWidget(topbar)

        # Content stack
        self.stack = QStackedWidget()
        self.tests_tab = TestsTab(db)
        self.students_tab = StudentsTab(db, config)
        self.results_tab = ResultsTab(db, config)
        self.stack.addWidget(self.tests_tab)
        self.stack.addWidget(self.students_tab)
        self.stack.addWidget(self.results_tab)
        root.addWidget(self.stack, 1)

        self.btn_tests.clicked.connect(lambda: self._switch(0))
        self.btn_users.clicked.connect(lambda: self._switch(1))
        self.btn_results.clicked.connect(lambda: self._switch(2))
        self._switch(0)

    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.btn_tests.setChecked(index == 0)
        self.btn_users.setChecked(index == 1)
        self.btn_results.setChecked(index == 2)
        if index == 1:
            self.students_tab.reload()
        elif index == 2:
            self.results_tab.reload()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("SecureTest Admin")
    app.setOrganizationName("SecureTest")

    try:
        config = load_config()
    except Exception as exc:
        logger.error("Конфіг: %s", exc)
        QMessageBox.critical(None, "Помилка", f"Не вдалося завантажити config.json:\n{exc}")
        return 1

    org = config.get("organization", {})
    org_name = org.get("name", "SecureTest")
    accent = org.get("accent_color", "#3b82f6")
    apply_theme(app, accent)

    # Пароль адмінки
    if not is_password_set(config):
        dlg = PasswordDialog(mode="setup")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0
        try:
            config = set_password(dlg.password(), config)
        except Exception as exc:
            QMessageBox.critical(None, "Помилка", f"Не вдалося зберегти пароль:\n{exc}")
            return 1
    else:
        dlg = PasswordDialog(mode="login")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0
        if not verify_password(dlg.password(), config):
            QMessageBox.critical(None, "Доступ", "Невірний пароль.")
            return 1

    try:
        db = get_db()
        logger.info("БД: %s", db.db_path)
    except Exception as exc:
        logger.exception("БД")
        QMessageBox.critical(None, "Помилка БД", str(exc))
        return 1

    window = MainWindow(db, config, org_name=org_name)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
