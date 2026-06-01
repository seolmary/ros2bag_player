"""PyQt5 video-player style control panel for :class:`PlayerCore`."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# Signed speed presets shown as buttons (negative = reverse).
SPEED_PRESETS = [-8.0, -4.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 4.0, 8.0]
STEP_SECONDS = 0.1          # how far the frame-step buttons move
SLIDER_TICKS = 100000       # slider resolution


def _fmt_time(seconds):
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60.0)
    return f'{int(m):02d}:{s:05.2f}'


class SeekSlider(QSlider):
    """A horizontal slider that jumps to the clicked position (no paging)."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            ratio = event.pos().x() / max(1, self.width())
            value = self.minimum() + round(ratio * (self.maximum() - self.minimum()))
            self.setValue(int(value))
            self.sliderPressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class ControlPanel(QWidget):
    def __init__(self, player):
        super().__init__()
        self.player = player
        self._scrubbing = False

        self.setWindowTitle('ROS 2 Bag Player')
        self.setMinimumWidth(640)
        self._build_ui()

        # Poll the engine to refresh the timeline / labels.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(33)  # ~30 Hz

        self._sync_speed_label()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QVBoxLayout(self)

        # --- timeline ---------------------------------------------------
        self.time_label = QLabel('00:00.00 / 00:00.00')
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet('font-family: monospace; font-size: 15px;')
        root.addWidget(self.time_label)

        self.slider = SeekSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_TICKS)
        self.slider.sliderPressed.connect(self._on_scrub_start)
        self.slider.sliderReleased.connect(self._on_scrub_end)
        self.slider.valueChanged.connect(self._on_slider_value)
        root.addWidget(self.slider)

        # --- transport buttons -----------------------------------------
        transport = QHBoxLayout()
        self.btn_start = QPushButton('⏮')          # |<
        self.btn_step_back = QPushButton('⏪')       # <<
        self.btn_play = QPushButton('▶')            # play / pause toggle
        self.btn_step_fwd = QPushButton('⏩')        # >>
        self.btn_end = QPushButton('⏭')             # >|
        for b in (self.btn_start, self.btn_step_back, self.btn_play,
                  self.btn_step_fwd, self.btn_end):
            b.setFixedHeight(40)
            b.setStyleSheet('font-size: 18px;')
            transport.addWidget(b)
        self.btn_play.setMinimumWidth(90)
        root.addLayout(transport)

        self.btn_start.clicked.connect(lambda: self.player.seek_fraction(0.0))
        self.btn_end.clicked.connect(lambda: self.player.seek_fraction(1.0))
        self.btn_step_back.clicked.connect(lambda: self.player.step(-STEP_SECONDS))
        self.btn_step_fwd.clicked.connect(lambda: self.player.step(+STEP_SECONDS))
        self.btn_play.clicked.connect(self._on_toggle)

        # --- speed ------------------------------------------------------
        speed_box = QGroupBox('Speed  (negative = reverse)')
        speed_layout = QGridLayout(speed_box)
        self.speed_label = QLabel('x1.0')
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setStyleSheet('font-weight: bold; font-size: 14px;')
        speed_layout.addWidget(self.speed_label, 0, 0, 1, len(SPEED_PRESETS))
        for i, preset in enumerate(SPEED_PRESETS):
            btn = QPushButton(f'{preset:g}x')
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _, p=preset: self._on_speed(p))
            speed_layout.addWidget(btn, 1, i)
        root.addWidget(speed_box)

        reverse_note = QLabel(
            'Note: reverse moves sim time backward, so RViz logs '
            '"jump back in time / Resetting" every frame — this is expected.')
        reverse_note.setWordWrap(True)
        reverse_note.setStyleSheet('color: gray; font-size: 11px;')
        root.addWidget(reverse_note)

        # --- options ----------------------------------------------------
        options = QHBoxLayout()
        self.loop_check = QCheckBox('Loop')
        self.loop_check.toggled.connect(self.player.set_loop)
        options.addWidget(self.loop_check)
        options.addStretch(1)
        hint = QLabel('Space: play/pause   ←/→: step   ↑/↓: speed')
        hint.setStyleSheet('color: gray;')
        options.addWidget(hint)
        root.addLayout(options)

        # --- per-topic toggles -----------------------------------------
        topics_box = QGroupBox('Topics')
        topics_outer = QVBoxLayout(topics_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(180)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        for topic in sorted(self.player.topic_types):
            type_str = self.player.topic_types[topic]
            cb = QCheckBox(f'{topic}   ({type_str})')
            cb.setChecked(topic in self.player._enabled)
            cb.toggled.connect(lambda state, t=topic: self.player.set_topic_enabled(t, state))
            inner_layout.addWidget(cb)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        topics_outer.addWidget(scroll)
        root.addWidget(topics_box)

    # ------------------------------------------------------------------ #
    # Slider / scrubbing
    # ------------------------------------------------------------------ #
    def _on_scrub_start(self):
        self._scrubbing = True

    def _on_scrub_end(self):
        self._scrubbing = False

    def _on_slider_value(self, value):
        # Only seek when the change came from the user dragging/clicking,
        # not from our own periodic refresh.
        if self._scrubbing:
            self.player.seek_fraction(value / SLIDER_TICKS)

    # ------------------------------------------------------------------ #
    # Transport / speed
    # ------------------------------------------------------------------ #
    def _on_toggle(self):
        # The ▶ button always resumes *forward*. Reverse is only entered
        # deliberately via the negative speed buttons. This keeps play/pause
        # intuitive and avoids accidentally resuming in reverse — reverse moves
        # sim time backward, which makes RViz reset its TF buffer every frame.
        if self.player.is_paused():
            magnitude = abs(self.player.get_rate_signed()) or 1.0
            self.player.set_rate_signed(magnitude)
            self.player.play()
        else:
            self.player.pause()
        self._sync_speed_label()
        self._update_play_button()

    def _on_speed(self, preset):
        self.player.set_rate_signed(preset)
        self.player.play()
        self._sync_speed_label()
        self._update_play_button()

    def _bump_speed(self, direction):
        """Move to the next/previous preset (used by the Up/Down keys)."""
        current = self.player.get_rate_signed()
        nearest = min(range(len(SPEED_PRESETS)),
                      key=lambda i: abs(SPEED_PRESETS[i] - current))
        nxt = max(0, min(len(SPEED_PRESETS) - 1, nearest + direction))
        self._on_speed(SPEED_PRESETS[nxt])

    def _sync_speed_label(self):
        rate = self.player.get_rate_signed()
        arrow = '◀ REVERSE' if rate < 0 else '▶'
        self.speed_label.setText(f'{arrow}  x{abs(rate):g}')

    def _update_play_button(self):
        self.btn_play.setText('▶' if self.player.is_paused() else '⏸')

    # ------------------------------------------------------------------ #
    # Periodic refresh
    # ------------------------------------------------------------------ #
    def _refresh(self):
        frac, elapsed, total = self.player.get_position()
        if not self._scrubbing:
            self.slider.blockSignals(True)
            self.slider.setValue(int(frac * SLIDER_TICKS))
            self.slider.blockSignals(False)
        self.time_label.setText(f'{_fmt_time(elapsed)} / {_fmt_time(total)}')
        self._update_play_button()

    # ------------------------------------------------------------------ #
    # Keyboard shortcuts
    # ------------------------------------------------------------------ #
    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Space:
            self._on_toggle()
        elif key == Qt.Key_Left:
            self.player.step(-STEP_SECONDS)
        elif key == Qt.Key_Right:
            self.player.step(+STEP_SECONDS)
        elif key == Qt.Key_Up:
            self._bump_speed(+1)
        elif key == Qt.Key_Down:
            self._bump_speed(-1)
        elif key == Qt.Key_Home:
            self.player.seek_fraction(0.0)
        elif key == Qt.Key_End:
            self.player.seek_fraction(1.0)
        else:
            super().keyPressEvent(event)
