"""PyQt5 video-player style control panel for :class:`PlayerCore`."""

import os

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from bag_player.bag_picker import pick_with_gui

# Signed speed presets shown as buttons (negative = reverse).
SPEED_PRESETS = [-8.0, -4.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 4.0, 8.0]
STEP_SECONDS = 0.1          # how far the frame-step buttons move
SLIDER_TICKS = 100000       # slider resolution


def _fmt_time(seconds):
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60.0)
    return f'{int(m):02d}:{s:05.2f}'


class SeekSlider(QSlider):
    """A horizontal slider where a click jumps to that position and dragging
    scrubs.

    Press/release go through ``setSliderDown()`` so ``sliderPressed`` and
    ``sliderReleased`` always fire in pairs — emitting ``sliderPressed`` by
    hand (without the slider ever being down) leaves the panel's scrubbing
    flag stuck and the bar stops following playback.
    """

    def _value_at(self, x):
        ratio = min(1.0, max(0.0, x / max(1, self.width())))
        return self.minimum() + round(ratio * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            self.setSliderDown(True)   # emits sliderPressed (scrubbing starts)
            self.setValue(int(self._value_at(event.pos().x())))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown() and (event.buttons() & Qt.LeftButton):
            self.setValue(int(self._value_at(event.pos().x())))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSliderDown():
            self.setSliderDown(False)  # emits sliderReleased (scrubbing ends)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ControlPanel(QWidget):
    def __init__(self, player):
        super().__init__()
        self.player = player
        self._scrubbing = False
        self._bag_generation = player.get_bag_generation()

        self.setMinimumWidth(640)
        self._build_ui()
        self._sync_bag_header()

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

        # --- which bag is loaded ----------------------------------------
        bag_row = QHBoxLayout()
        self.bag_label = QLabel()
        self.bag_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.bag_label.setStyleSheet('color: gray; font-size: 11px;')
        bag_row.addWidget(self.bag_label, 1)
        btn_open = QPushButton('Open bag…')
        btn_open.setFocusPolicy(Qt.NoFocus)  # keep Space as play/pause
        btn_open.clicked.connect(self._on_open_bag)
        bag_row.addWidget(btn_open)
        root.addLayout(bag_row)

        # --- timeline ---------------------------------------------------
        self.time_label = QLabel('00:00.00 / 00:00.00')
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet('font-family: monospace; font-size: 15px;')
        root.addWidget(self.time_label)

        self.slider = SeekSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_TICKS)
        # Never let the slider take keyboard focus: a focused QSlider consumes
        # the arrow keys, which are the panel's frame-step shortcuts.
        self.slider.setFocusPolicy(Qt.NoFocus)
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
        self._topics_scroll = QScrollArea()
        self._topics_scroll.setWidgetResizable(True)
        self._topics_scroll.setFrameShape(QFrame.NoFrame)
        self._topics_scroll.setMaximumHeight(180)
        self._populate_topics()
        topics_outer.addWidget(self._topics_scroll)
        root.addWidget(topics_box)

    def _populate_topics(self):
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        for topic in sorted(self.player.topic_types):
            type_str = self.player.topic_types[topic]
            cb = QCheckBox(f'{topic}   ({type_str})')
            cb.setChecked(topic in self.player._enabled)
            cb.toggled.connect(lambda state, t=topic: self.player.set_topic_enabled(t, state))
            inner_layout.addWidget(cb)
        inner_layout.addStretch(1)
        self._topics_scroll.setWidget(inner)  # setWidget drops the old one

    def _sync_bag_header(self):
        bag_name = os.path.basename(os.path.normpath(self.player.bag_uri))
        self.setWindowTitle(f'ROS 2 Bag Player — {bag_name}')
        self.bag_label.setText(self.player.bag_uri)
        self.bag_label.setToolTip(self.player.bag_uri)

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
    # Switching bags at runtime
    # ------------------------------------------------------------------ #
    def _on_open_bag(self):
        self.player.pause()
        self._update_play_button()
        root = os.environ.get('BAG_SEARCH_ROOT', '~/ros2bag')
        bag = pick_with_gui(root, initial=self.player.bag_uri)
        if bag is not None:
            # Async: the play thread swaps the reader; _refresh() picks up the
            # generation bump (or the error) and updates the widgets.
            self.player.request_load(bag.path, bag.storage_id)

    def _on_bag_changed(self):
        # A switch must always re-park the bar, even if a slider release went
        # missing (e.g. the mouse was let go outside the window).
        self._scrubbing = False
        self._sync_bag_header()
        self._populate_topics()
        self._update_play_button()
        self._remember_bag()

    def _remember_bag(self):
        """Keep `run.sh --last` and the picker default in sync with switches."""
        cache = os.path.join(
            os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')),
            'bag_player', 'last_bag')
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, 'w') as f:
                f.write(f'{self.player.bag_uri}\t{self.player.storage_id}\n')
        except OSError:
            pass

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
        generation = self.player.get_bag_generation()
        if generation != self._bag_generation:
            self._bag_generation = generation
            self._on_bag_changed()
        error = self.player.take_load_error()
        if error:
            QMessageBox.warning(self, 'Could not open bag', error)

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
