"""
Екран проходження тесту: одне питання, таймер, автозбереження.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.db import Database
from core.ui_theme import logo_pixmap, repolish
from core.scoring import score_attempt
from client.monitor import PeriodicScreenshot, ProcessMonitor
from client.screenshots import capture_screenshot

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class QuizScreen(QWidget):
    """Повноекранний інтерфейс проходження тесту."""

    def __init__(
        self,
        db: Database,
        config: dict[str, Any],
        student: dict,
        attempt_id: int,
        test: dict,
        questions: list[dict],
        on_finished: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.student = student
        self.attempt_id = attempt_id
        self.test = test
        self.questions = questions  # already ordered / shuffled
        self.on_finished = on_finished
        self.partial_credit = bool(config.get("partial_credit", False))
        self.violation_threshold = int(test.get("violation_threshold") or 5)
        self.screenshot_quality = int(config.get("screenshot_quality", 70))
        self._violation_count = 0
        self._on_violation_external = None  # optional callback from main (lockdown)
        self._ignore_focus = False  # ігнорувати втрату фокуса під час діалогів

        self._index = 0
        self._answers: dict[int, str] = {}  # question_id → selected JSON/text
        self._question_seconds: dict[int, int] = {}  # question_id → seconds
        self._current_q_start: datetime | None = None
        self._finished = False

        # Завантажити вже збережені відповіді (на випадок відновлення — поки просто)
        for row in db.fetchall(
            "SELECT question_id, selected FROM answers WHERE attempt_id = ?",
            (attempt_id,),
        ):
            self._answers[row["question_id"]] = row["selected"] or ""

        for row in db.fetchall(
            "SELECT question_id, seconds FROM question_times WHERE attempt_id = ?",
            (attempt_id,),
        ):
            self._question_seconds[row["question_id"]] = int(row["seconds"] or 0)

        self._build_ui()
        self._start_timers()
        self._start_monitors()
        self._show_question(0)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(48, 24, 48, 24)
        root.setSpacing(12)

        # Логотип + назва (компактний хедер)
        header = QHBoxLayout()
        header.setSpacing(10)
        logo_label = QLabel()
        try:
            pixmap = logo_pixmap("logo-client.svg", 32)
            if not pixmap.isNull():
                logo_label.setPixmap(pixmap)
        except Exception:
            pass
        header.addWidget(logo_label)
        name_label = QLabel("QuizForge")
        name_label.setObjectName("logoLabel")
        header.addWidget(name_label)
        header.addStretch()
        root.addLayout(header)

        # Дебаг-бар (режим розробника)
        self.debug_bar = QLabel("")
        self.debug_bar.setObjectName("debugBar")
        self.debug_bar.setWordWrap(True)
        self._dev_mode = bool(self.config.get("developer_mode"))
        self.debug_bar.setVisible(self._dev_mode)
        root.addWidget(self.debug_bar)

        # Верхня панель: прогрес + таймер
        top = QHBoxLayout()
        self.progress_label = QLabel()
        self.progress_label.setObjectName("progressLabel")
        self.progress_label.setWordWrap(False)
        top.addWidget(self.progress_label)
        top.addSpacing(16)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(320)
        top.addWidget(self.progress_bar)
        top.addStretch()
        self.timer_label = QLabel()
        self.timer_label.setObjectName("timerLabel")
        self.timer_label.setProperty("low", False)
        top.addWidget(self.timer_label)
        root.addLayout(top)

        # Текст питання
        self.question_label = QLabel()
        self.question_label.setWordWrap(True)
        self.question_label.setObjectName("questionLabel")
        self.question_label.setSizePolicy(self.question_label.sizePolicy().horizontalPolicy(), self.question_label.sizePolicy().verticalPolicy())
        root.addWidget(self.question_label)

        # Область відповідей
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        self.answers_container = QWidget()
        self.answers_layout = QVBoxLayout(self.answers_container)
        self.answers_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.answers_layout.setSpacing(8)
        scroll.setWidget(self.answers_container)
        root.addWidget(scroll, stretch=1)

        # Навігація
        nav = QHBoxLayout()
        nav.setSpacing(12)
        self.btn_prev = QPushButton("← Назад")
        self.btn_prev.setObjectName("secondaryButton")
        self.btn_prev.setMinimumHeight(48)
        self.btn_prev.setMinimumWidth(120)
        self.btn_prev.clicked.connect(self._prev)
        self.btn_next = QPushButton("Далі →")
        self.btn_next.setObjectName("primaryButton")
        self.btn_next.setMinimumHeight(48)
        self.btn_next.setMinimumWidth(120)
        self.btn_next.clicked.connect(self._next)
        self.btn_finish = QPushButton("Завершити тест")
        self.btn_finish.setObjectName("dangerButton")
        self.btn_finish.setMinimumHeight(48)
        self.btn_finish.setMinimumWidth(160)
        self.btn_finish.clicked.connect(self._confirm_finish)

        nav.addWidget(self.btn_prev)
        nav.addStretch()
        nav.addWidget(self.btn_finish)
        nav.addStretch()
        nav.addWidget(self.btn_next)
        root.addLayout(nav)

        self._option_widgets: list[QWidget] = []
        self._button_group: QButtonGroup | None = None
        self._open_edit: QLineEdit | None = None

    def _start_timers(self) -> None:
        limit_min = int(self.test.get("time_limit_minutes") or 60)
        self._seconds_left = limit_min * 60
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start(1000)
        self._update_timer_label()
        self._update_debug()

    def _on_tick(self) -> None:
        if self._finished:
            return
        self._seconds_left -= 1
        # час на поточне питання
        if self._current_q_start and self.questions:
            qid = self.questions[self._index]["id"]
            self._question_seconds[qid] = self._question_seconds.get(qid, 0) + 1
        self._update_timer_label()
        if self._seconds_left <= 0:
            self._auto_finish()

    def _update_debug(self) -> None:
        if not getattr(self, "_dev_mode", False):
            return
        q = self.questions[self._index] if self.questions else {}
        self.debug_bar.setText(
            f"DEV | Q {self._index + 1}/{len(self.questions)} "
            f"| id={q.get('id', '—')} type={q.get('qtype', '—')} "
            f"| violations={self._violation_count} "
            f"| time={self._seconds_left}s "
            f"| attempt={self.attempt_id}"
        )

    def toggle_dev_mode(self) -> None:
        self._dev_mode = not self._dev_mode
        self.debug_bar.setVisible(self._dev_mode)
        if self._dev_mode:
            self._update_debug()

    def _update_timer_label(self) -> None:
        s = max(0, self._seconds_left)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h > 0:
            self.timer_label.setText(f"⏱ {h:02d}:{m:02d}:{sec:02d}")
        else:
            self.timer_label.setText(f"⏱ {m:02d}:{sec:02d}")
        if s <= 60:
            if not self.timer_label.property("low"):
                self.timer_label.setProperty("low", True)
                repolish(self.timer_label)

    def _show_question(self, index: int) -> None:
        if not self.questions:
            return
        # Зберегти час попереднього
        self._save_current_answer()
        self._persist_question_time()

        self._index = index
        q = self.questions[index]
        self._current_q_start = datetime.now(timezone.utc)

        total = len(self.questions)
        self.progress_label.setText(f"Питання {index + 1} з {total}")
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(index + 1)
        self.question_label.setText(q["text"])
        self._update_debug()

        # Очистити варіанти
        while self.answers_layout.count():
            item = self.answers_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._option_widgets.clear()
        self._button_group = None
        self._open_edit = None

        qtype = q["qtype"]
        saved = self._answers.get(q["id"], "")

        if qtype == "open":
            self._open_edit = QLineEdit()
            self._open_edit.setPlaceholderText("Ваша відповідь…")
            self._open_edit.setMinimumHeight(44)
            self._open_edit.setStyleSheet("font-size: 16px;")
            if saved:
                self._open_edit.setText(saved)
            self._open_edit.textChanged.connect(lambda: self._save_current_answer())
            self.answers_layout.addWidget(self._open_edit)
        else:
            opts = list(
                self.db.fetchall(
                    "SELECT id, text, is_correct, order_index FROM options "
                    "WHERE question_id = ? ORDER BY order_index, id",
                    (q["id"],),
                )
            )
            if self.test.get("shuffle_options"):
                random.shuffle(opts)

            try:
                selected_ids = set(json.loads(saved or "[]"))
            except (json.JSONDecodeError, TypeError):
                selected_ids = set()

            if qtype == "single":
                self._button_group = QButtonGroup(self)
                self._button_group.setExclusive(True)
                for opt in opts:
                    rb = QRadioButton(opt["text"])
                    rb.setObjectName("optionCard")
                    rb.setCursor(Qt.CursorShape.PointingHandCursor)
                    rb.setProperty("option_id", opt["id"])
                    if opt["id"] in selected_ids:
                        rb.setChecked(True)
                    rb.toggled.connect(lambda checked: self._save_current_answer() if checked else None)
                    self._button_group.addButton(rb)
                    self.answers_layout.addWidget(rb)
                    self._option_widgets.append(rb)
            else:  # multiple
                for opt in opts:
                    cb = QCheckBox(opt["text"])
                    cb.setObjectName("optionCard")
                    cb.setCursor(Qt.CursorShape.PointingHandCursor)
                    cb.setProperty("option_id", opt["id"])
                    if opt["id"] in selected_ids:
                        cb.setChecked(True)
                    cb.stateChanged.connect(lambda: self._save_current_answer())
                    self.answers_layout.addWidget(cb)
                    self._option_widgets.append(cb)

        self.btn_prev.setEnabled(index > 0)
        self.btn_next.setEnabled(index < total - 1)

    def _collect_answer(self) -> str:
        q = self.questions[self._index]
        qtype = q["qtype"]
        if qtype == "open":
            return (self._open_edit.text() if self._open_edit else "").strip()
        ids = []
        for w in self._option_widgets:
            if isinstance(w, QRadioButton) and w.isChecked():
                ids.append(w.property("option_id"))
            elif isinstance(w, QCheckBox) and w.isChecked():
                ids.append(w.property("option_id"))
        return json.dumps(ids)

    def _save_current_answer(self) -> None:
        if self._finished or not self.questions:
            return
        q = self.questions[self._index]
        selected = self._collect_answer()
        self._answers[q["id"]] = selected
        # UPSERT у БД
        existing = self.db.fetchone(
            "SELECT id FROM answers WHERE attempt_id = ? AND question_id = ?",
            (self.attempt_id, q["id"]),
        )
        if existing:
            self.db.update(
                "answers",
                {"selected": selected},
                "id = ?",
                (existing["id"],),
            )
        else:
            self.db.insert(
                "answers",
                {
                    "attempt_id": self.attempt_id,
                    "question_id": q["id"],
                    "selected": selected,
                    "is_correct": None,
                    "points_awarded": 0,
                },
            )

    def _persist_question_time(self) -> None:
        if not self.questions or self._index >= len(self.questions):
            return
        qid = self.questions[self._index]["id"]
        secs = self._question_seconds.get(qid, 0)
        existing = self.db.fetchone(
            "SELECT id FROM question_times WHERE attempt_id = ? AND question_id = ?",
            (self.attempt_id, qid),
        )
        if existing:
            self.db.update(
                "question_times",
                {"seconds": secs},
                "id = ?",
                (existing["id"],),
            )
        else:
            self.db.insert(
                "question_times",
                {
                    "attempt_id": self.attempt_id,
                    "question_id": qid,
                    "seconds": secs,
                },
            )

    def _prev(self) -> None:
        if self._index > 0:
            self._show_question(self._index - 1)

    def _next(self) -> None:
        if self._index < len(self.questions) - 1:
            self._show_question(self._index + 1)

    def _unanswered_count(self) -> int:
        count = 0
        for q in self.questions:
            val = self._answers.get(q["id"], "")
            if q["qtype"] == "open":
                if not val.strip():
                    count += 1
            else:
                try:
                    ids = json.loads(val or "[]")
                except (json.JSONDecodeError, TypeError):
                    ids = []
                if not ids:
                    count += 1
        return count

    def _confirm_finish(self) -> None:
        unanswered = self._unanswered_count()
        msg = "Завершити тест?"
        if unanswered:
            msg = (
                f"Є {unanswered} питань без відповіді.\n"
                "Після завершення змінити відповіді буде неможливо.\n\n"
                "Завершити тест?"
            )
        self._ignore_focus = True
        try:
            reply = QMessageBox.question(
                self,
                "Завершення",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        finally:
            self._ignore_focus = False
        if reply == QMessageBox.StandardButton.Yes:
            self._finish()

    def _auto_finish(self) -> None:
        if self._finished:
            return
        self._ignore_focus = True
        try:
            QMessageBox.information(
                self,
                "Час вичерпано",
                "Час тесту закінчився. Тест завершується автоматично.",
            )
        finally:
            self._ignore_focus = False
        self._finish()

    def _start_monitors(self) -> None:
        blacklist = self.config.get("process_blacklist") or []
        self._process_monitor = ProcessMonitor(
            blacklist=blacklist,
            on_violation=self._on_blacklist_process,
            interval_sec=5,
            parent=self,
        )
        self._process_monitor.start()

        interval = int(self.config.get("screenshot_interval_sec", 45))
        self._periodic_ss = PeriodicScreenshot(
            interval_sec=interval,
            on_capture=self._periodic_screenshot,
            parent=self,
        )
        self._periodic_ss.start()

    def _stop_monitors(self) -> None:
        if hasattr(self, "_process_monitor"):
            self._process_monitor.stop()
        if hasattr(self, "_periodic_ss"):
            self._periodic_ss.stop()

    def set_violation_callback(self, cb) -> None:
        """Дозволяє зовнішній callback (наприклад, від lockdown)."""
        self._on_violation_external = cb

    def record_violation(self, vtype: str, details: str = "", silent: bool = False) -> None:
        """Записує порушення і показує попередження (без блокування)."""
        if self._finished:
            return
        self._violation_count += 1
        qnum = self._index + 1 if self.questions else None
        student_id = self.student.get("id") or 0

        sid = capture_screenshot(
            self.db,
            self.attempt_id,
            quality=self.screenshot_quality,
            is_periodic=False,
        )

        vid = self.db.insert(
            "violations",
            {
                "attempt_id": self.attempt_id,
                "student_id": student_id,
                "vtype": vtype,
                "occurred_at": _utc_now_iso(),
                "question_number": qnum,
                "details": details or "",
                "screenshot_id": sid,
            },
        )
        if sid:
            self.db.update("screenshots", {"violation_id": vid}, "id = ?", (sid,))

        logger.warning(
            "Порушення #%s: %s (%s) attempt=%s",
            self._violation_count,
            vtype,
            details,
            self.attempt_id,
        )

        if self._on_violation_external:
            try:
                self._on_violation_external(vtype, details)
            except Exception:
                pass

        if silent:
            return

        # Попередження замість блокування
        labels = {
            "focus_lost": "Спроба згорнути або перемкнути вікно",
            "blocked_key": f"Натиснуто заборонену комбінацію: {details}",
            "blacklist_process": f"Виявлено сторонню програму: {details}",
            "emergency_close": "Спроба аварійного закриття",
        }
        text = labels.get(vtype, f"Зафіксовано порушення: {details or vtype}")
        self._ignore_focus = True
        try:
            QMessageBox.warning(
                self,
                "Попередження",
                f"{text}\n\nПорушення записано в звіт.",
            )
        finally:
            self._ignore_focus = False

    def handle_blocked_key(self, combo: str) -> None:
        """Діалог для будь-якої забороненої комбінації."""
        if self._finished:
            return
        self.record_violation("blocked_key", combo, silent=True)
        self._ignore_focus = True
        try:
            reply = QMessageBox.question(
                self,
                "Порушення",
                f"Порушення: натиснуто комбінацію клавіш «{combo}».\n"
                "Бажаєте завершити тест?\n\n"
                "Завершити — тест завершиться, результати не зараховуються, "
                "звіт буде надіслано.\n"
                "Продовжити — тест продовжиться, порушення записано в звіт.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            # Перейменовуємо кнопки через setButtonText якщо можливо
            # (стандартні Yes/No залишаємо, текст у повідомленні пояснює)
        finally:
            self._ignore_focus = False
        if reply == QMessageBox.StandardButton.Yes:
            self._finish(zero_score=True, user_aborted=True)

    def handle_alt_f4(self) -> None:
        """Сумісність: перенаправляє на загальний обробник."""
        self.handle_blocked_key("Alt+F4")

    def _on_blacklist_process(self, process_name: str) -> None:
        self.record_violation("blacklist_process", process_name)

    def _periodic_screenshot(self) -> None:
        if self._finished:
            return
        capture_screenshot(
            self.db,
            self.attempt_id,
            quality=self.screenshot_quality,
            is_periodic=True,
        )

    def _finish(self, blocked: bool = False, zero_score: bool = False, user_aborted: bool = False) -> None:
        if self._finished:
            return
        self._finished = True
        self._tick_timer.stop()
        self._stop_monitors()
        self._save_current_answer()
        self._persist_question_time()

        # Зберегти час по всіх питаннях
        for qid, secs in self._question_seconds.items():
            existing = self.db.fetchone(
                "SELECT id FROM question_times WHERE attempt_id = ? AND question_id = ?",
                (self.attempt_id, qid),
            )
            if existing:
                self.db.update("question_times", {"seconds": secs}, "id = ?", (existing["id"],))
            else:
                self.db.insert(
                    "question_times",
                    {"attempt_id": self.attempt_id, "question_id": qid, "seconds": secs},
                )

        end_time = _utc_now_iso()
        status = "aborted" if user_aborted else "completed"
        self.db.update(
            "attempts",
            {"status": status, "end_time": end_time},
            "id = ?",
            (self.attempt_id,),
        )

        try:
            if zero_score or user_aborted:
                self.db.update(
                    "attempts",
                    {"score": 0, "percent": 0},
                    "id = ?",
                    (self.attempt_id,),
                )
            else:
                score_attempt(self.db, self.attempt_id, partial_credit=self.partial_credit)
        except Exception:
            logger.exception("Помилка підрахунку балів attempt=%s", self.attempt_id)

        # PDF-звіт + розсилка
        try:
            from core.report_builder import build_report, send_report
            report_path = build_report(self.db, self.attempt_id)
            logger.info("Звіт збережено: %s", report_path)
            sent = send_report(self.db, self.config, self.attempt_id, report_path)
            if not sent:
                logger.warning(
                    "Звіт не відправлено (збережено локально). "
                    "Буде повтор при наступному запуску."
                )
        except Exception:
            logger.exception("Помилка формування/відправки звіту attempt=%s", self.attempt_id)

        self.on_finished()


class FinishedScreen(QWidget):
    """Екран після завершення тесту."""

    def __init__(
        self,
        config: dict[str, Any],
        score_info: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addStretch(1)

        card = QWidget()
        card.setMaximumWidth(480)
        card.setMinimumWidth(320)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(16)
        card_layout.setContentsMargins(8, 8, 8, 8)

        # Логотип + назва
        logo_layout = QHBoxLayout()
        logo_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_layout.setSpacing(12)
        logo_label = QLabel()
        try:
            pixmap = logo_pixmap("logo-client.svg", 48)
            if not pixmap.isNull():
                logo_label.setPixmap(pixmap)
        except Exception:
            pass
        logo_layout.addWidget(logo_label)
        name_label = QLabel("QuizForge")
        name_label.setObjectName("logoLabel")
        logo_layout.addWidget(name_label)
        card_layout.addLayout(logo_layout)

        title = QLabel("Тест завершено")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        card_layout.addWidget(title)

        thanks = QLabel("Дякуємо за участь!")
        thanks.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thanks.setWordWrap(True)
        thanks.setObjectName("bodyLabel")
        card_layout.addWidget(thanks)

        btn_close = QPushButton("Закрити")
        btn_close.setObjectName("primaryButton")
        btn_close.setMinimumHeight(48)
        btn_close.clicked.connect(self._close_app)
        card_layout.addWidget(btn_close)

        layout.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

    def _close_app(self) -> None:
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()
