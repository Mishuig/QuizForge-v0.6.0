"""
Спільна тема QuizForge (адмін-панель і клієнт).

Один QSS-шаблон `client/ui/styles.qss` із плейсхолдерами @ACCENT@ тощо.
Акцентний колір береться з config.json -> organization.accent_color.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontDatabase, QPixmap
from PyQt6.QtWidgets import QApplication, QTableWidgetItem, QWidget

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
QSS_DARK = ROOT / "client" / "ui" / "styles.qss"
QSS_LIGHT = ROOT / "client" / "ui" / "styles-light.qss"
DEFAULT_ACCENT = "#3b82f6"

# Кольори статусів (текст на темному тлі, контраст ≥ 4.5:1)
STATUS_COLORS = {
    "ok": "#6EE7B7",
    "info": "#93C5FD",
    "warn": "#FDE68A",
    "bad": "#FCA5A5",
    "muted": "#B4C0D4",
}


def _shade(hex_color: str, factor: float) -> str:
    c = QColor(hex_color)
    if not c.isValid():
        c = QColor(DEFAULT_ACCENT)
    if factor >= 1:
        c = c.lighter(int(factor * 100))
    else:
        c = c.darker(int(100 / max(factor, 0.01)))
    return c.name()


def _load_fonts(app: QApplication) -> None:
    """Підключає Inter з папки fonts/, якщо файли там є; інакше — системний шрифт."""
    family = None
    for path in sorted((ROOT / "fonts").glob("Inter*.[ot]tf")):
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid != -1 and not family:
            fams = QFontDatabase.applicationFontFamilies(fid)
            family = fams[0] if fams else None
    if family:
        app.setFont(QFont(family, 10))


def apply_theme(app: QApplication, accent: str | None = None, theme: str = "dark") -> None:
    """Застосовує тему до всього застосунку. theme: 'dark' | 'light'"""
    accent = accent or DEFAULT_ACCENT
    theme = theme if theme in ("dark", "light") else "dark"
    if not QColor(accent).isValid():
        accent = DEFAULT_ACCENT
    _load_fonts(app)
    if not QSS_PATH.is_file():
        logger.warning("Файл стилів не знайдено: %s", QSS_PATH)
        return
    qss = QSS_PATH.read_text(encoding="utf-8")
    tokens = {
        "@ACCENT@": accent,
        "@ACCENT_HOVER@": _shade(accent, 1.12),
        "@ACCENT_PRESSED@": _shade(accent, 0.85),
        "@ASSETS@": (ASSETS / "ui").as_posix(),
    }
    soft = QColor(accent)
    soft.setAlpha(0x2E)
    tokens["@ACCENT_SOFT@"] = f"rgba({soft.red()},{soft.green()},{soft.blue()},{soft.alpha()})"
    for key, val in tokens.items():
        qss = qss.replace(key, val)
    app.setStyleSheet(qss)


def logo_pixmap(name: str, size: int) -> QPixmap:
    """Логотип з assets/ (шлях не залежить від поточної директорії)."""
    pm = QPixmap(str(ASSETS / name))
    if pm.isNull():
        return pm
    return pm.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def repolish(widget: QWidget) -> None:
    """Перезастосовує QSS після зміни динамічної властивості."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def status_item(text: str, kind: str = "muted") -> QTableWidgetItem:
    """Комірка таблиці зі статусом: «● Текст» кольором статусу."""
    item = QTableWidgetItem(f"● {text}")
    item.setForeground(QBrush(QColor(STATUS_COLORS.get(kind, STATUS_COLORS["muted"]))))
    font = item.font()
    font.setWeight(QFont.Weight.DemiBold)
    item.setFont(font)
    return item


def colored_item(text: str, kind: str, bold: bool = True) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setForeground(QBrush(QColor(STATUS_COLORS.get(kind, STATUS_COLORS["muted"]))))
    if bold:
        font = item.font()
        font.setWeight(QFont.Weight.DemiBold)
        item.setFont(font)
    return item
