import os
import sys
import json
import hashlib
import wave
from dataclasses import dataclass

import pygame
from PyQt6.QtCore import QByteArray, QEasingCurve, QEvent, QPropertyAnimation, QRect, QRectF, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDial,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from audio_cache import ProcessedSoundCache
from audio_models import (
    DEFAULT_COMPRESSOR_SETTINGS,
    compressor_settings_from_dict,
    compressor_settings_to_dict,
)
from audio_processing import CompressorEngine

# Try importing pycaw for Windows volume control
try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False
    print("Warning: pycaw/comtypes not found. 'Smart Unmute/Remute' feature will be disabled.")


THEME = {
    "bg": "#0f131a",
    "bg_alt": "#141b25",
    "surface": "#1b2431",
    "surface_raised": "#212d3d",
    "border": "#2e3f55",
    "fg": "#e8eef7",
    "muted": "#9fb1c7",
    "btn_base": "#2a3648",
    "btn_highlight": "#33445d",
    "btn_shadow": "#0a0f16",
    "btn_outline": "#4a607e",
    "stop_btn": "#d5425f",
    "stop_btn_highlight": "#e15572",
    "stop_btn_outline": "#ff8aa0",
    "accent": "#53e0d3",
}


@dataclass
class SoundItem:
    key: str
    display_name: str
    sound: pygame.mixer.Sound
    file: str
    length: float
    group: str | None = None


class CircularButton(QPushButton):
    def __init__(
        self,
        text,
        diameter=94,
        color=THEME["btn_base"],
        outline=THEME["btn_outline"],
        hover_color=THEME["btn_highlight"],
        text_color=THEME["fg"],
        role="default",
        parent=None,
    ):
        super().__init__(parent)
        self._raw_text = text
        self._diameter = diameter
        self._color = color
        self._outline = outline
        self._hover_color = hover_color
        self._text_color = text_color
        self._role = role

        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Segoe UI Semibold", 9))
        self.setToolTip(text)
        self.setText(self._wrap_text(self._raw_text))
        self._apply_style()

    def _apply_style(self):
        radius = self._diameter // 2
        pressed_color = THEME["btn_shadow"] if self._role != "stop" else "#9f2d43"
        style = f"""
            QPushButton {{
                background-color: {self._color};
                color: {self._text_color};
                border: 2px solid {self._outline};
                border-radius: {radius}px;
                padding: 8px;
                text-align: center;
            }}
            QPushButton:hover {{
                background-color: {self._hover_color};
            }}
            QPushButton:pressed {{
                background-color: {pressed_color};
                padding-top: 10px;
                padding-bottom: 6px;
            }}
        """
        self.setStyleSheet(style)

    def _wrap_text(self, text):
        max_width = max(20, self._diameter - 24)
        metrics = QFontMetrics(self.font())

        words = text.split()
        if not words:
            return text

        lines = []
        current = ""

        for word in words:
            candidate = word if not current else f"{current} {word}"
            if metrics.horizontalAdvance(candidate) <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)

            # Fallback for single long words with no spaces.
            if metrics.horizontalAdvance(word) > max_width:
                chunk = ""
                for ch in word:
                    chunk_candidate = f"{chunk}{ch}"
                    if metrics.horizontalAdvance(chunk_candidate) <= max_width:
                        chunk = chunk_candidate
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = ch
                current = chunk
            else:
                current = word

        if current:
            lines.append(current)

        return "\n".join(lines)


class HorizontalDragDial(QDial):
    valueDoubleClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_active = False
        self._start_x = 0
        self._start_value = 0
        self._pixels_per_step = 2

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._start_x = int(event.position().x())
            self._start_value = self.value()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.buttons() & Qt.MouseButton.LeftButton):
            delta_x = int(event.position().x()) - self._start_x
            steps = int(delta_x / self._pixels_per_step)
            self.setValue(self._start_value + (steps * self.singleStep()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            center = self.rect().center()
            dx = event.position().x() - center.x()
            dy = event.position().y() - center.y()
            if (dx * dx) + (dy * dy) <= 22 * 22:
                self.valueDoubleClicked.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class DialControl(QWidget):
    valueChanged = pyqtSignal(float)

    def __init__(self, label, minimum, maximum, step, decimals, tooltip="", parent=None):
        super().__init__(parent)
        self.setObjectName("DialControl")
        self._decimals = decimals
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._factor = 10**decimals

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        title = QLabel(label)
        title.setObjectName("CompressorLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        if tooltip:
            title.setToolTip(tooltip)
        layout.addWidget(title)

        self.dial = HorizontalDragDial()
        self.dial.setNotchesVisible(True)
        self.dial.setWrapping(False)
        self.dial.setFixedSize(72, 72)
        self.dial.setRange(int(round(minimum * self._factor)), int(round(maximum * self._factor)))
        self.dial.setSingleStep(max(1, int(round(step * self._factor))))
        self.dial.valueChanged.connect(self._on_dial_value_changed)
        self.dial.valueDoubleClicked.connect(self._enable_text_edit)
        layout.addWidget(self.dial, 0, Qt.AlignmentFlag.AlignHCenter)

        self.value_edit = QLineEdit(self.dial)
        self.value_edit.setObjectName("DialValue")
        self.value_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_edit.setFixedSize(44, 22)
        self.value_edit.setReadOnly(True)
        self.value_edit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.value_edit.editingFinished.connect(self._apply_text_edit)
        self._center_value_editor()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_value_editor()

    def _center_value_editor(self):
        center_x = (self.dial.width() - self.value_edit.width()) // 2
        center_y = (self.dial.height() - self.value_edit.height()) // 2
        self.value_edit.move(center_x, center_y)

    def set_value(self, value):
        clamped = min(self._maximum, max(self._minimum, float(value)))
        raw_value = int(round(clamped * self._factor))
        self.dial.blockSignals(True)
        self.dial.setValue(raw_value)
        self.dial.blockSignals(False)
        self._sync_text(clamped)

    def _on_dial_value_changed(self, raw_value):
        value = raw_value / self._factor
        self._sync_text(value)
        self.valueChanged.emit(value)

    def _sync_text(self, value):
        self.value_edit.setText(f"{value:.{self._decimals}f}")

    def _enable_text_edit(self):
        self.value_edit.setReadOnly(False)
        self.value_edit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.value_edit.setFocus()
        self.value_edit.selectAll()

    def _apply_text_edit(self):
        if self.value_edit.isReadOnly():
            return

        try:
            value = float(self.value_edit.text().strip())
        except ValueError:
            value = self.dial.value() / self._factor

        clamped = min(self._maximum, max(self._minimum, value))
        self.value_edit.setReadOnly(True)
        self.value_edit.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.dial.setValue(int(round(clamped * self._factor)))


class YellowBlackToggle(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(72, 34)
        self._progress = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._start_transition)

    @staticmethod
    def _blend(a: QColor, b: QColor, t: float) -> QColor:
        clamped = max(0.0, min(1.0, float(t)))
        r = int(round((a.red() * (1.0 - clamped)) + (b.red() * clamped)))
        g = int(round((a.green() * (1.0 - clamped)) + (b.green() * clamped)))
        b_val = int(round((a.blue() * (1.0 - clamped)) + (b.blue() * clamped)))
        a_val = int(round((a.alpha() * (1.0 - clamped)) + (b.alpha() * clamped)))
        return QColor(r, g, b_val, a_val)

    def _start_transition(self, checked):
        target = 1.0 if checked else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def get_progress(self):
        return self._progress

    def set_progress(self, value):
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    progress = pyqtProperty(float, fget=get_progress, fset=set_progress)

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = self.rect().adjusted(1, 1, -1, -1)
        rect_f = QRectF(rect)
        p = self._progress

        # Inset slot/groove
        painter.setPen(QPen(QColor("#020407"), 1))
        painter.setBrush(QColor("#0a0e14"))
        painter.drawRoundedRect(rect_f, rect.height() / 2, rect.height() / 2)

        groove = rect.adjusted(2, 2, -2, -2)
        groove_f = QRectF(groove)
        off_track = QColor(THEME["surface"])
        on_track_top = QColor("#f7dc53")
        on_track_bottom = QColor("#d3bc33")
        track_top = self._blend(off_track, on_track_top, p)
        track_bottom = self._blend(off_track, on_track_bottom, p)
        text_color = self._blend(QColor("#0b0f15"), QColor("#151920"), p)
        track_grad = QLinearGradient(0, groove.top(), 0, groove.bottom())
        track_grad.setColorAt(0.0, track_top)
        track_grad.setColorAt(1.0, track_bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_grad)
        painter.drawRoundedRect(groove_f, groove.height() / 2, groove.height() / 2)

        # Knob
        knob_size = groove.height() - 4
        knob_min_x = groove.left() + 2
        knob_max_x = groove.right() - knob_size - 2
        knob_x = int(round(knob_min_x + ((knob_max_x - knob_min_x) * p)))
        knob_y = groove.top() + 2
        painter.setPen(QPen(QColor("#161b23"), 1))
        painter.setBrush(QColor("#2e333c"))
        painter.drawEllipse(knob_x, knob_y, knob_size, knob_size)
        painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
        painter.drawArc(knob_x + 2, knob_y + 2, knob_size - 4, knob_size - 4, 30 * 16, 120 * 16)

        # ON/OFF labels slide with knob and are clipped to stay within the toggle track.
        painter.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(groove_f, groove.height() / 2, groove.height() / 2)
        painter.setClipPath(clip_path)

        font = QFont("Segoe UI Semibold", 10)
        painter.setFont(font)
        fm = painter.fontMetrics()
        label_h = groove.height()
        on_w = max(24, fm.horizontalAdvance("ON") + 6)
        off_w = max(26, fm.horizontalAdvance("OFF") + 6)

        knob_center_x = knob_x + (knob_size / 2.0)
        label_offset = (knob_size / 2.0) + 15.0
        on_center_x = int(round(knob_center_x - label_offset))
        off_center_x = int(round(knob_center_x + label_offset))

        on_rect = QRect(on_center_x - (on_w // 2), groove.top(), on_w, label_h)
        off_rect = QRect(off_center_x - (off_w // 2), groove.top(), off_w, label_h)

        on_alpha = int(round(255 * p))
        off_alpha = int(round(255 * (1.0 - p)))

        shadow_on = QColor(0, 0, 0, int(round(90 * (on_alpha / 255.0))))
        shadow_off = QColor(0, 0, 0, int(round(90 * (off_alpha / 255.0))))
        on_text = QColor(text_color.red(), text_color.green(), text_color.blue(), on_alpha)
        off_text = QColor(text_color.red(), text_color.green(), text_color.blue(), off_alpha)

        painter.setPen(shadow_on)
        painter.drawText(on_rect.adjusted(0, 1, 0, 1), Qt.AlignmentFlag.AlignCenter, "ON")
        painter.setPen(on_text)
        painter.drawText(on_rect, Qt.AlignmentFlag.AlignCenter, "ON")

        painter.setPen(shadow_off)
        painter.drawText(off_rect.adjusted(0, 1, 0, 1), Qt.AlignmentFlag.AlignCenter, "OFF")
        painter.setPen(off_text)
        painter.drawText(off_rect, Qt.AlignmentFlag.AlignCenter, "OFF")
        painter.restore()

class SoundboardWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Python Soundboard")
        self.resize(900, 680)
        self.setMinimumSize(560, 460)

        self.app_dir = os.path.dirname(os.path.abspath(__file__))
        self.sounds_dir = os.path.join(self.app_dir, "sounds")
        self.settings_path = os.path.join(self.app_dir, "settings.json")
        self.processed_cache_dir = os.path.join(self.app_dir, ".processed_cache")
        os.makedirs(self.processed_cache_dir, exist_ok=True)

        pygame.mixer.init()
        pygame.mixer.set_num_channels(32)

        self.sound_index = {}
        self.grouped_sounds = {}
        self.ungrouped_sounds = []

        self.group_order = []
        self.group_sections = []  # [(QGroupBox, QGridLayout, [CircularButton])]
        self.ungrouped_section = None  # (QFrame, QGridLayout, [CircularButton])

        self.current_cols = 0
        self.max_cols_seen = 0
        self.pending_layout_width = 0

        self.opt_overlap = True
        self.opt_smart_mute = False
        self.saved_geometry_b64 = None
        self.remute_pending = False
        self.compressor_settings = compressor_settings_from_dict({})
        self.processed_cache = ProcessedSoundCache(self.compressor_settings.cache_max_items)
        self.compressor_engine = CompressorEngine()
        self._compressor_updating_ui = False
        self.compressor_controls = {}
        self.compressor_collapsed = False

        self.layout_timer = QTimer(self)
        self.layout_timer.setSingleShot(True)
        self.layout_timer.timeout.connect(self.apply_layout)

        self.remute_timer = QTimer(self)
        self.remute_timer.setInterval(100)
        self.remute_timer.timeout.connect(self.check_remute)

        self._build_ui()
        self.load_settings()
        self._sync_compressor_controls()
        self._sync_option_actions()
        self._restore_geometry()

        self.load_sounds()
        self.rebuild_sound_widgets()
        self.schedule_layout()

    def _build_ui(self):
        self._apply_app_style()
        self._create_menu()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(8)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)

        title = QLabel("SOUNDBOARD")
        title.setObjectName("HeaderTitle")
        subtitle = QLabel("Play clips instantly with overlap, grouping, and smart remute.")
        subtitle.setObjectName("HeaderSubtitle")

        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        self.scroll_shell = QFrame()
        self.scroll_shell.setObjectName("Shell")
        shell_layout = QVBoxLayout(self.scroll_shell)
        shell_layout.setContentsMargins(1, 1, 1, 1)
        shell_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.content_layout.setSpacing(10)

        self.scroll_area.setWidget(self.content)
        self.scroll_area.viewport().installEventFilter(self)
        shell_layout.addWidget(self.scroll_area)
        root.addWidget(self.scroll_shell, 1)

        self.stop_shell = QFrame()
        self.stop_shell.setObjectName("Shell")
        stop_shell_layout = QVBoxLayout(self.stop_shell)
        stop_shell_layout.setContentsMargins(1, 1, 1, 1)

        stop_bar = QWidget()
        stop_bar_layout = QHBoxLayout(stop_bar)
        stop_bar_layout.setContentsMargins(6, 6, 6, 6)
        stop_bar_layout.setSpacing(12)

        self.compressor_panel = self._build_compressor_panel()
        stop_bar_layout.addWidget(self.compressor_panel, 0, Qt.AlignmentFlag.AlignLeft)
        stop_bar_layout.addStretch(1)

        self.stop_btn = CircularButton(
            "STOP ALL",
            diameter=86,
            color=THEME["stop_btn"],
            hover_color=THEME["stop_btn_highlight"],
            outline=THEME["stop_btn_outline"],
            role="stop",
        )
        self.stop_btn.clicked.connect(self.stop_all_sounds)

        stop_bar_layout.addWidget(self.stop_btn)
        stop_bar_layout.addStretch(1)
        stop_shell_layout.addWidget(stop_bar)
        root.addWidget(self.stop_shell)

    def _apply_app_style(self):
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background-color: {THEME['bg']};
                color: {THEME['fg']};
            }}
            QWidget {{
                background-color: {THEME['bg']};
                color: {THEME['fg']};
                font-family: 'Segoe UI';
                font-size: 10pt;
            }}
            QLabel#HeaderTitle {{
                font-size: 13pt;
                font-weight: 700;
                letter-spacing: 1px;
                color: {THEME['fg']};
            }}
            QLabel#HeaderSubtitle {{
                font-size: 9pt;
                color: {THEME['muted']};
            }}
            QFrame#Shell {{
                background-color: {THEME['border']};
                border: 0;
            }}
            QScrollArea {{
                background-color: {THEME['surface']};
                border: 0;
            }}
            QScrollArea > QWidget > QWidget {{
                background-color: {THEME['surface']};
            }}
            QScrollBar:vertical {{
                background: {THEME['bg_alt']};
                width: 12px;
                margin: 2px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {THEME['btn_highlight']};
                min-height: 24px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {THEME['accent']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
                background: none;
            }}
            QGroupBox {{
                background-color: {THEME['surface_raised']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 8px;
                font-size: 10pt;
                font-weight: 600;
                color: {THEME['accent']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }}
            QFrame#CompressorPanel {{
                background-color: {THEME['surface']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
            }}
            QLabel#CompressorTitle {{
                font-size: 9pt;
                font-weight: 700;
                color: {THEME['accent']};
                background: transparent;
            }}
            QLabel#CompressorLabel {{
                font-size: 8.5pt;
                color: {THEME['muted']};
            }}
            QWidget#DialControl {{
                background: transparent;
            }}
            QWidget#CompressorControlsContainer {{
                background: transparent;
            }}
            QWidget#CompressorToggleCollapsed {{
                background: transparent;
            }}
            QWidget#CompressorToggleExpanded {{
                background: transparent;
            }}
            QLineEdit#DialValue {{
                background-color: rgba(10, 15, 22, 210);
                border: 1px solid {THEME['btn_outline']};
                border-radius: 10px;
                color: {THEME['fg']};
                font-size: 8pt;
                padding: 0px 3px;
            }}
            QMenuBar {{
                background: {THEME['surface']};
                color: {THEME['fg']};
            }}
            QMenuBar::item:selected {{
                background: {THEME['btn_highlight']};
            }}
            QMenu {{
                background: {THEME['surface']};
                color: {THEME['fg']};
                border: 1px solid {THEME['border']};
            }}
            QMenu::item:selected {{
                background: {THEME['btn_highlight']};
            }}
            """
        )

    def _create_menu(self):
        menubar = self.menuBar()
        options_menu = QMenu("Options", self)

        self.reload_action = QAction("Reload Audio Files", self)
        self.reload_action.triggered.connect(self.reload_sounds)
        options_menu.addAction(self.reload_action)
        options_menu.addSeparator()

        self.smart_action = QAction("Smart Unmute/Remute", self)
        self.smart_action.setCheckable(True)
        self.smart_action.setEnabled(PYCAW_AVAILABLE)
        self.smart_action.toggled.connect(self.on_smart_mute_toggled)
        options_menu.addAction(self.smart_action)

        options_menu.addSeparator()

        self.playback_group = QActionGroup(self)
        self.playback_group.setExclusive(True)

        self.overlap_action = QAction("Overlap Audio", self)
        self.overlap_action.setCheckable(True)
        self.overlap_action.triggered.connect(lambda: self.on_playback_mode_selected(True))

        self.interrupt_action = QAction("Interrupt Previous", self)
        self.interrupt_action.setCheckable(True)
        self.interrupt_action.triggered.connect(lambda: self.on_playback_mode_selected(False))

        self.playback_group.addAction(self.overlap_action)
        self.playback_group.addAction(self.interrupt_action)

        options_menu.addAction(self.overlap_action)
        options_menu.addAction(self.interrupt_action)

        menubar.addMenu(options_menu)

    def _build_compressor_panel(self):
        panel = QFrame()
        panel.setObjectName("CompressorPanel")
        panel.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.compressor_collapse_btn = QPushButton("-")
        self.compressor_collapse_btn.setFixedWidth(28)
        self.compressor_collapse_btn.clicked.connect(self.toggle_compressor_panel_collapsed)
        title_row.addWidget(self.compressor_collapse_btn)

        title = QLabel("Compressor")
        title.setObjectName("CompressorTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)

        self.compressor_reset_btn = QPushButton("Reset")
        self.compressor_reset_btn.clicked.connect(self.reset_compressor_defaults)
        title_row.addWidget(self.compressor_reset_btn)

        layout.addLayout(title_row)

        self.compressor_enabled_toggle = YellowBlackToggle()
        self.compressor_enabled_toggle.toggled.connect(self.on_compressor_enabled_toggled)

        self.compressor_toggle_collapsed = QWidget()
        self.compressor_toggle_collapsed.setObjectName("CompressorToggleCollapsed")
        self.compressor_toggle_collapsed_layout = QVBoxLayout(self.compressor_toggle_collapsed)
        self.compressor_toggle_collapsed_layout.setContentsMargins(2, 2, 2, 2)
        self.compressor_toggle_collapsed_layout.setSpacing(0)
        self.compressor_toggle_collapsed_layout.addStretch(1)
        self.compressor_toggle_collapsed_layout.addStretch(1)
        layout.addWidget(self.compressor_toggle_collapsed)

        self.compressor_controls_container = QWidget()
        self.compressor_controls_container.setObjectName("CompressorControlsContainer")
        controls_grid = QGridLayout(self.compressor_controls_container)
        controls_grid.setContentsMargins(0, 0, 0, 0)
        controls_grid.setHorizontalSpacing(8)
        controls_grid.setVerticalSpacing(8)

        specs = [
            ("Input", "input_gain_db", -24.0, 24.0, 0.1, 1, "Boosts or lowers incoming level before compression."),
            ("Threshold", "threshold_db", -48.0, 0.0, 0.1, 1, "Level where compression starts."),
            ("Ratio", "ratio", 1.0, 20.0, 0.1, 1, "How strongly audio above threshold is reduced."),
            ("Attack", "attack_ms", 1.0, 250.0, 1.0, 0, "How quickly compression engages after a peak."),
            ("Release", "release_ms", 10.0, 1000.0, 1.0, 0, "How quickly compression relaxes after peaks drop."),
            ("Makeup", "makeup_gain_db", -12.0, 24.0, 0.1, 1, "Output gain applied after compression."),
            ("Ceiling", "output_ceiling_db", -24.0, 0.0, 0.1, 1, "Maximum output level limiter cap."),
        ]

        for i, (label, attr, minimum, maximum, step, decimals, tooltip) in enumerate(specs):
            row = i // 4
            col = i % 4
            self._add_compressor_control(
                controls_grid,
                row=row,
                col=col,
                label=label,
                attr=attr,
                minimum=minimum,
                maximum=maximum,
                step=step,
                decimals=decimals,
                tooltip=tooltip,
            )

        self.compressor_toggle_expanded = QWidget()
        self.compressor_toggle_expanded.setObjectName("CompressorToggleExpanded")
        self.compressor_toggle_expanded_layout = QVBoxLayout(self.compressor_toggle_expanded)
        self.compressor_toggle_expanded_layout.setContentsMargins(2, 2, 2, 2)
        self.compressor_toggle_expanded_layout.setSpacing(0)
        self.compressor_toggle_expanded_layout.addStretch(1)
        self.compressor_toggle_expanded_layout.addStretch(1)
        controls_grid.addWidget(self.compressor_toggle_expanded, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.compressor_controls_container)
        self._apply_compressor_panel_state()

        return panel

    def toggle_compressor_panel_collapsed(self):
        self.compressor_collapsed = not self.compressor_collapsed
        self._apply_compressor_panel_state()

    def _apply_compressor_panel_state(self):
        is_collapsed = bool(self.compressor_collapsed)
        self.compressor_collapse_btn.setText("+" if is_collapsed else "-")
        self.compressor_reset_btn.setVisible(not is_collapsed)
        self.compressor_controls_container.setVisible(not is_collapsed)
        self.compressor_toggle_collapsed.setVisible(is_collapsed)
        self._mount_compressor_toggle(collapsed=is_collapsed)

    def _mount_compressor_toggle(self, collapsed):
        self.compressor_toggle_collapsed_layout.removeWidget(self.compressor_enabled_toggle)
        self.compressor_toggle_expanded_layout.removeWidget(self.compressor_enabled_toggle)

        target_layout = self.compressor_toggle_collapsed_layout if collapsed else self.compressor_toggle_expanded_layout
        target_layout.insertWidget(1, self.compressor_enabled_toggle, 0, Qt.AlignmentFlag.AlignHCenter)

    def _add_compressor_control(self, grid, row, col, label, attr, minimum, maximum, step, decimals, tooltip):
        control = DialControl(
            label=label,
            minimum=minimum,
            maximum=maximum,
            step=step,
            decimals=decimals,
            tooltip=tooltip,
        )
        control.valueChanged.connect(lambda v, name=attr: self.on_compressor_dial_changed(name, v))
        grid.addWidget(control, row, col)
        self.compressor_controls[attr] = control

    def _sync_compressor_controls(self):
        self._compressor_updating_ui = True
        try:
            self.compressor_enabled_toggle.setChecked(self.compressor_settings.enabled)
            for attr, control in self.compressor_controls.items():
                control.set_value(float(getattr(self.compressor_settings, attr)))
        finally:
            self._compressor_updating_ui = False

    def _clear_processed_cache(self, increment_revision=True):
        if increment_revision:
            self.compressor_settings.revision += 1
        self.processed_cache.clear()

    def _compressor_signature(self):
        return "|".join(
            (
                f"ig={self.compressor_settings.input_gain_db:.3f}",
                f"th={self.compressor_settings.threshold_db:.3f}",
                f"ra={self.compressor_settings.ratio:.3f}",
                f"at={self.compressor_settings.attack_ms:.3f}",
                f"re={self.compressor_settings.release_ms:.3f}",
                f"mk={self.compressor_settings.makeup_gain_db:.3f}",
                f"ce={self.compressor_settings.output_ceiling_db:.3f}",
            )
        )

    def _disk_cache_filename(self, sound_data, signature):
        try:
            stat = os.stat(sound_data.file)
            stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            stamp = "0:0"
        raw_key = f"{sound_data.file}|{stamp}|{signature}"
        digest = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()
        return os.path.join(self.processed_cache_dir, f"{digest}.wav")

    def _load_cached_sound_from_disk(self, sound_data, signature):
        cache_file = self._disk_cache_filename(sound_data, signature)
        if not os.path.isfile(cache_file):
            return None
        try:
            print(f"Loaded processed audio from disk cache: {sound_data.key}")
            return pygame.mixer.Sound(cache_file)
        except (pygame.error, OSError) as e:
            self.log_error(f"Could not load disk cache for {sound_data.key}", e)
            return None

    def _save_cached_sound_to_disk(self, sound_data, signature, sound):
        cache_file = self._disk_cache_filename(sound_data, signature)
        try:
            samples = pygame.sndarray.array(sound)
            if samples.ndim == 1:
                channels = 1
            else:
                channels = samples.shape[1]
            with wave.open(cache_file, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(samples.dtype.itemsize)
                wf.setframerate(pygame.mixer.get_init()[0])
                wf.writeframes(samples.tobytes())
        except (pygame.error, OSError, wave.Error) as e:
            self.log_error(f"Could not save disk cache for {sound_data.key}", e)

    def _clear_disk_cache(self):
        try:
            for filename in os.listdir(self.processed_cache_dir):
                if filename.lower().endswith(".wav"):
                    full_path = os.path.join(self.processed_cache_dir, filename)
                    try:
                        os.remove(full_path)
                    except OSError as e:
                        self.log_error(f"Could not remove cache file {filename}", e)
        except OSError as e:
            self.log_error("Could not clear disk cache folder", e)

    def on_compressor_enabled_toggled(self, checked):
        if self._compressor_updating_ui:
            return

        self.compressor_settings.enabled = bool(checked)
        print(f"Compressor {'enabled' if self.compressor_settings.enabled else 'disabled'}")
        self.save_settings()

    def on_compressor_dial_changed(self, attr, value):
        if self._compressor_updating_ui:
            return

        self._set_compressor_attr(attr, value)

    def _set_compressor_attr(self, attr, value):
        setattr(self.compressor_settings, attr, float(value))
        if attr == "cache_max_items":
            self.processed_cache.set_capacity(int(self.compressor_settings.cache_max_items))
        self._clear_processed_cache(increment_revision=True)
        self._clear_disk_cache()
        self.save_settings()

    def reset_compressor_defaults(self):
        self.compressor_settings = compressor_settings_from_dict(compressor_settings_to_dict(DEFAULT_COMPRESSOR_SETTINGS))
        self.processed_cache.set_capacity(self.compressor_settings.cache_max_items)
        self._clear_processed_cache(increment_revision=True)
        self._clear_disk_cache()
        self._sync_compressor_controls()
        self.save_settings()

    def load_settings(self):
        if not os.path.isfile(self.settings_path):
            return

        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.log_error("Could not read settings file", e)
            return

        overlap = data.get("overlap_audio")
        smart_mute = data.get("smart_unmute_remute")
        geometry_b64 = data.get("window_geometry_b64")
        self.compressor_settings = compressor_settings_from_dict(data)
        self.processed_cache.set_capacity(self.compressor_settings.cache_max_items)

        if isinstance(overlap, bool):
            self.opt_overlap = overlap
        if isinstance(smart_mute, bool):
            self.opt_smart_mute = smart_mute
        if isinstance(geometry_b64, str) and geometry_b64:
            self.saved_geometry_b64 = geometry_b64

    def _restore_geometry(self):
        if not self.saved_geometry_b64:
            return

        try:
            geometry_bytes = QByteArray.fromBase64(self.saved_geometry_b64.encode("ascii"))
            self.restoreGeometry(geometry_bytes)
        except Exception as e:
            self.log_error("Could not restore window geometry", e)

    def save_settings(self):
        geometry_b64 = bytes(self.saveGeometry().toBase64()).decode("ascii")
        data = {
            "overlap_audio": bool(self.opt_overlap),
            "smart_unmute_remute": bool(self.opt_smart_mute),
            "window_geometry_b64": geometry_b64,
        }
        data.update(compressor_settings_to_dict(self.compressor_settings))
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            self.log_error("Could not save settings file", e)

    def _sync_option_actions(self):
        for action, checked in (
            (self.smart_action, self.opt_smart_mute),
            (self.overlap_action, self.opt_overlap),
            (self.interrupt_action, not self.opt_overlap),
        ):
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

    def on_playback_mode_selected(self, overlap):
        self.opt_overlap = bool(overlap)
        self.save_settings()

    def on_smart_mute_toggled(self, checked):
        self.opt_smart_mute = bool(checked)
        self.save_settings()

    def eventFilter(self, obj, event):
        if obj is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self.schedule_layout(event.size().width())
        return super().eventFilter(obj, event)

    def load_sounds(self):
        valid_exts = (".mp3", ".wav", ".ogg")
        self.sound_index.clear()
        self.grouped_sounds.clear()
        self.ungrouped_sounds.clear()

        if not os.path.isdir(self.sounds_dir):
            self.log_error("Sounds folder not found", self.sounds_dir)
            return

        total_loaded = 0
        try:
            entries = sorted(os.listdir(self.sounds_dir), key=str.lower)
        except OSError as e:
            self.log_error("Could not read sounds folder", e)
            return

        for item in entries:
            full_item_path = os.path.join(self.sounds_dir, item)

            if os.path.isfile(full_item_path) and item.lower().endswith(valid_exts):
                sound_item = self._load_sound(full_item_path, item, os.path.splitext(item)[0], None)
                if sound_item:
                    self.sound_index[sound_item.key] = sound_item
                    self.ungrouped_sounds.append(sound_item)
                    total_loaded += 1
            elif os.path.isdir(full_item_path):
                group_name = item
                try:
                    group_files = sorted(
                        [f for f in os.listdir(full_item_path) if f.lower().endswith(valid_exts)],
                        key=str.lower,
                    )
                except OSError as e:
                    self.log_error(f"Could not read group folder {group_name}", e)
                    continue

                if not group_files:
                    continue

                self.grouped_sounds[group_name] = []
                for filename in group_files:
                    full_path = os.path.join(full_item_path, filename)
                    key = os.path.join(group_name, filename)
                    sound_item = self._load_sound(full_path, key, os.path.splitext(filename)[0], group_name)
                    if sound_item:
                        self.sound_index[sound_item.key] = sound_item
                        self.grouped_sounds[group_name].append(sound_item)
                        total_loaded += 1

        print(f"Found {total_loaded} audio files.")

    def _load_sound(self, full_path, key, display_name, group):
        try:
            sound = pygame.mixer.Sound(full_path)
        except (pygame.error, OSError) as e:
            self.log_error(f"Could not load {key}", e)
            return None

        return SoundItem(
            key=key,
            display_name=display_name,
            sound=sound,
            file=full_path,
            length=sound.get_length(),
            group=group,
        )

    def rebuild_sound_widgets(self):
        self._clear_content_layout()
        self.group_sections.clear()
        self.ungrouped_section = None

        self.group_order = sorted(self.grouped_sounds.keys(), key=str.lower)

        for group_name in self.group_order:
            box = QGroupBox(group_name)
            grid = QGridLayout(box)
            grid.setContentsMargins(10, 10, 10, 10)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)

            buttons = []
            for sound_meta in self.grouped_sounds[group_name]:
                btn = CircularButton(sound_meta.display_name, parent=box)
                btn.clicked.connect(lambda checked=False, k=sound_meta.key: self.play_sound(k))
                buttons.append(btn)

            self.content_layout.addWidget(box)
            self.group_sections.append((box, grid, buttons))

        if self.ungrouped_sounds:
            panel = QFrame()
            panel.setStyleSheet(
                f"QFrame {{ background-color: {THEME['surface']}; border: 1px solid {THEME['border']}; border-radius: 8px; }}"
            )
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(10, 10, 10, 10)
            panel_layout.setSpacing(8)

            label = QLabel("Ungrouped")
            label.setStyleSheet(f"color: {THEME['muted']}; font-weight: 600;")
            panel_layout.addWidget(label)

            ungrouped_grid = QGridLayout()
            ungrouped_grid.setHorizontalSpacing(10)
            ungrouped_grid.setVerticalSpacing(10)
            panel_layout.addLayout(ungrouped_grid)

            buttons = []
            for sound_meta in self.ungrouped_sounds:
                btn = CircularButton(sound_meta.display_name, parent=panel)
                btn.clicked.connect(lambda checked=False, k=sound_meta.key: self.play_sound(k))
                buttons.append(btn)

            self.content_layout.addWidget(panel)
            self.ungrouped_section = (panel, ungrouped_grid, buttons)

        self.content_layout.addStretch(1)

    def _clear_content_layout(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def schedule_layout(self, width=None):
        if width is None or width <= 0:
            width = self.scroll_area.viewport().width()
        self.pending_layout_width = width
        self.layout_timer.start(25)

    def apply_layout(self):
        width = max(1, self.pending_layout_width)
        cols = max(1, width // 118)

        for _, grid, buttons in self.group_sections:
            self._layout_buttons(grid, buttons, cols)

        if self.ungrouped_section:
            _, grid, buttons = self.ungrouped_section
            self._layout_buttons(grid, buttons, cols)

        self.current_cols = cols

    def _layout_buttons(self, grid, buttons, cols):
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()

        max_cols = max(self.max_cols_seen, cols)
        for c in range(max_cols):
            grid.setColumnStretch(c, 1 if c < cols else 0)
        self.max_cols_seen = max_cols

        for i, btn in enumerate(buttons):
            row = i // cols
            col = i % cols
            grid.addWidget(btn, row, col, alignment=Qt.AlignmentFlag.AlignHCenter)
            btn.show()

    def reload_sounds(self):
        self.stop_all_sounds()
        self._clear_processed_cache(increment_revision=False)
        self.load_sounds()
        self.rebuild_sound_widgets()
        self.schedule_layout()

    def get_system_volume_interface(self):
        if not PYCAW_AVAILABLE:
            return None

        device = AudioUtilities.GetSpeakers()
        if hasattr(device, "EndpointVolume"):
            return device.EndpointVolume

        interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return interface.QueryInterface(IAudioEndpointVolume)

    def play_sound(self, sound_key):
        sound_data = self.sound_index.get(sound_key)
        if not sound_data:
            return

        if not self.opt_overlap:
            pygame.mixer.stop()

        if self.opt_smart_mute and PYCAW_AVAILABLE:
            try:
                volume = self.get_system_volume_interface()
                if volume and volume.GetMute():
                    volume.SetMute(0, None)
                    self.remute_pending = True
                    if not self.remute_timer.isActive():
                        self.remute_timer.start()
            except (AttributeError, OSError) as e:
                self.log_error("Volume control error", e)

        sound_to_play = sound_data.sound
        if self.compressor_settings.enabled:
            signature = self._compressor_signature()
            cache_key = (sound_key, signature)
            try:
                cached = self.processed_cache.get(cache_key)
                if cached is None:
                    cached = self._load_cached_sound_from_disk(sound_data, signature)
                    if cached is None:
                        print(f"Processing audio: {sound_key} ({signature})")
                        cached = self.compressor_engine.process(sound_data.sound, self.compressor_settings)
                        self._save_cached_sound_to_disk(sound_data, signature, cached)
                    self.processed_cache.put(cache_key, cached)
                sound_to_play = cached
            except Exception as e:
                self.log_error(f"Compressor processing failed for {sound_key}", e)
                sound_to_play = sound_data.sound

        sound_to_play.play()

    def check_remute(self):
        if not self.remute_pending:
            self.remute_timer.stop()
            return

        if pygame.mixer.get_busy():
            return

        try:
            volume = self.get_system_volume_interface()
            if volume:
                volume.SetMute(1, None)
            self.remute_pending = False
            self.remute_timer.stop()
        except (AttributeError, OSError) as e:
            self.log_error("Volume remute error", e)

    def stop_all_sounds(self):
        pygame.mixer.stop()
        if self.remute_pending and not self.remute_timer.isActive():
            self.remute_timer.start()

    def closeEvent(self, event):
        self.save_settings()
        self.remute_timer.stop()
        try:
            pygame.mixer.stop()
            pygame.mixer.quit()
        except pygame.error:
            pass
        event.accept()

    def log_error(self, context, error):
        print(f"{context}: {error}")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = SoundboardWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
