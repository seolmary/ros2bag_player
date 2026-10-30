"""PyQt5 video-player style control panel for :class:`PlayerCore`."""

import os

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bag_player.bag_picker import pick_with_gui

try:
    import qtawesome as qta
except ImportError:
    qta = None

SPEED_PRESETS = [-8.0, -4.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 4.0, 8.0]
SLIDER_TICKS = 100000

ICON_COLOR = '#333333'
TIMELINE_QSS = '''
QSlider::groove:horizontal { height: 8px; border-radius: 4px; background: #C9CED4; }
QSlider::sub-page:horizontal { border-radius: 4px; background: #2E9E6B; }
QSlider::handle:horizontal { width: 18px; height: 18px; margin: -5px 0;
                             border-radius: 9px; background: #FFFFFF; border: 1px solid #8A9099; }
'''


def _fmt_time(seconds):
    seconds = max(0.0, seconds)
    m, s = divmod(seconds, 60.0)
    return f'{int(m):02d}:{s:05.2f}'


def _short_type(type_str):
    return type_str.replace('/msg/', '/')


class SeekSlider(QSlider):
    """Click jumps, drag scrubs. Press/release go through setSliderDown() so
    sliderPressed/sliderReleased always pair up."""

    def _value_at(self, x):
        ratio = min(1.0, max(0.0, x / max(1, self.width())))
        return self.minimum() + round(ratio * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            self.setSliderDown(True)
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
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ControlPanel(QWidget):
    def __init__(self, player):
        super().__init__()
        self.player = player
        self._scrubbing = False
        self._bag_generation = player.get_bag_generation()

        self.setMinimumWidth(720)
        self._build_ui()
        self._sync_bag_header()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(33)

        self._sync_speed()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _icon(self, name):
        return qta.icon(f'fa5s.{name}', color=ICON_COLOR) if qta else None

    def _button(self, icon, fallback, tip, text=''):
        b = QPushButton(text)
        if qta:
            b.setIcon(self._icon(icon))
            b.setIconSize(QSize(20, 20))
        elif not text:
            b.setText(fallback)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # --- which bag is loaded ----------------------------------------
        bag_row = QHBoxLayout()
        self.bag_label = QLabel()
        self.bag_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.bag_label.setStyleSheet('color: gray; font-size: 11px;')
        bag_row.addWidget(self.bag_label, 1)
        btn_open = self._button('folder-open', '', 'Open another bag', text='Open bag…')
        btn_open.clicked.connect(self._on_open_bag)
        bag_row.addWidget(btn_open)
        root.addLayout(bag_row)

        # --- timeline ---------------------------------------------------
        self.time_label = QLabel('00:00.00 / 00:00.00')
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet('font-family: monospace; font-size: 24px; font-weight: bold;')
        root.addWidget(self.time_label)

        self.slider = SeekSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_TICKS)
        self.slider.setStyleSheet(TIMELINE_QSS)
        self.slider.setFocusPolicy(Qt.NoFocus)  # a focused slider would eat the arrow-key shortcuts
        self.slider.sliderPressed.connect(self._on_scrub_start)
        self.slider.sliderReleased.connect(self._on_scrub_end)
        self.slider.valueChanged.connect(self._on_slider_value)
        root.addWidget(self.slider)

        # --- transport buttons -----------------------------------------
        transport = QHBoxLayout()
        self.btn_start = self._button('step-backward', '⏮', 'Go to start (Home)')
        self.btn_step_back = self._button('backward', '⏪', 'Previous frame of the selected topic (←)')
        self.btn_play = self._button('play', '▶', 'Play / pause (Space)')
        self.btn_step_fwd = self._button('forward', '⏩', 'Next frame of the selected topic (→)')
        self.btn_end = self._button('step-forward', '⏭', 'Go to end (End)')
        for b in (self.btn_start, self.btn_step_back, self.btn_play,
                  self.btn_step_fwd, self.btn_end):
            b.setFixedHeight(44)
            b.setStyleSheet('font-size: 18px;')
            transport.addWidget(b)
        self.btn_play.setMinimumWidth(110)
        self.btn_play.setIconSize(QSize(24, 24))
        root.addLayout(transport)

        self.btn_start.clicked.connect(lambda: self.player.seek_fraction(0.0))
        self.btn_end.clicked.connect(lambda: self.player.seek_fraction(1.0))
        self.btn_step_back.clicked.connect(lambda: self._step(-1))
        self.btn_step_fwd.clicked.connect(lambda: self._step(+1))
        self.btn_play.clicked.connect(self._on_toggle)

        # --- speed ------------------------------------------------------
        speed_box = QGroupBox('Speed')
        speed_box.setToolTip(
            'Left of centre = reverse. Reverse moves sim time backward, so RViz '
            'logs "jump back in time / Resetting" a few times a second — this is expected.')
        speed_layout = QVBoxLayout(speed_box)
        speed_row = QHBoxLayout()
        self.speed_label = QLabel()
        self.speed_label.setMinimumWidth(120)
        self.speed_label.setStyleSheet('font-weight: bold; font-size: 14px;')
        speed_row.addWidget(self.speed_label)
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, len(SPEED_PRESETS) - 1)
        self.speed_slider.setPageStep(1)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.setTickInterval(1)
        self.speed_slider.setFocusPolicy(Qt.NoFocus)
        self.speed_slider.valueChanged.connect(lambda i: self._on_speed(SPEED_PRESETS[i]))
        speed_row.addWidget(self.speed_slider, 1)
        speed_layout.addLayout(speed_row)
        ticks = QHBoxLayout()
        ticks.addSpacing(self.speed_label.minimumWidth() + speed_row.spacing())
        for preset in SPEED_PRESETS:
            lab = QLabel(f'{preset:g}x')
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet('color: gray; font-size: 10px;')
            ticks.addWidget(lab, 1)
        speed_layout.addLayout(ticks)
        root.addWidget(speed_box)

        # --- options ----------------------------------------------------
        options = QHBoxLayout()
        self.loop_check = QCheckBox('Loop')
        self.loop_check.setFocusPolicy(Qt.NoFocus)
        self.loop_check.toggled.connect(self.player.set_loop)
        options.addWidget(self.loop_check)
        options.addStretch(1)
        hint = QLabel('Space: play/pause   ←/→: frame   ↑/↓: speed   Home/End')
        hint.setStyleSheet('color: gray;')
        options.addWidget(hint)
        root.addLayout(options)

        # --- per-topic toggles -----------------------------------------
        topics_box = QGroupBox('Topics')
        topics_layout = QVBoxLayout(topics_box)
        tools = QHBoxLayout()
        self.topic_filter = QLineEdit()
        self.topic_filter.setPlaceholderText('Filter topics…')
        self.topic_filter.setClearButtonEnabled(True)
        self.topic_filter.textChanged.connect(self._filter_topics)
        tools.addWidget(self.topic_filter, 1)
        for text, enabled in (('All', True), ('None', False)):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(f'{"Enable" if enabled else "Disable"} every listed topic')
            b.clicked.connect(lambda _, e=enabled: self._set_all_topics(e))
            tools.addWidget(b)
        topics_layout.addLayout(tools)
        self.topics = QTreeWidget()
        self.topics.setHeaderLabels(['Topic', 'Type', 'Msgs'])
        self.topics.setRootIsDecorated(False)
        self.topics.setAlternatingRowColors(True)
        self.topics.setFocusPolicy(Qt.NoFocus)
        self.topics.setMinimumHeight(180)
        self.topics.header().setStretchLastSection(False)
        self.topics.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.topics.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.topics.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.topics.itemChanged.connect(self._on_topic_item)
        topics_layout.addWidget(self.topics)
        root.addWidget(topics_box, 1)
        self._populate_topics()

    def _populate_topics(self):
        counts = self.player.topic_counts
        self.topics.blockSignals(True)
        self.topics.clear()
        for topic in sorted(self.player.topic_types):
            item = QTreeWidgetItem([topic, _short_type(self.player.topic_types[topic]),
                                    f'{counts.get(topic, 0):,}'])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if topic in self.player._enabled else Qt.Unchecked)
            item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            self.topics.addTopLevelItem(item)
        clouds = [i for i in self._topic_items() if i.text(1).endswith('/PointCloud2')]
        if clouds:
            self.topics.setCurrentItem(max(clouds, key=lambda i: counts.get(i.text(0), 0)))
        self.topics.blockSignals(False)
        self._filter_topics(self.topic_filter.text())

    def _topic_items(self):
        return [self.topics.topLevelItem(i) for i in range(self.topics.topLevelItemCount())]

    def _step(self, direction):
        item = self.topics.currentItem()
        self.player.step_frame(direction, item.text(0) if item else None)

    def _on_topic_item(self, item, column):
        if column == 0:
            self.player.set_topic_enabled(item.text(0), item.checkState(0) == Qt.Checked)

    def _filter_topics(self, text):
        text = text.lower()
        for item in self._topic_items():
            item.setHidden(text not in item.text(0).lower() and text not in item.text(1).lower())

    def _set_all_topics(self, enabled):
        for item in self._topic_items():
            if not item.isHidden():
                item.setCheckState(0, Qt.Checked if enabled else Qt.Unchecked)

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
            self.player.request_load(bag.path, bag.storage_id)

    def _on_bag_changed(self):
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
        if self.player.is_paused():  # play always resumes forward; reverse only via the speed slider
            magnitude = abs(self.player.get_rate_signed()) or 1.0
            self.player.set_rate_signed(magnitude)
            self.player.play()
        else:
            self.player.pause()
        self._sync_speed()
        self._update_play_button()

    def _on_speed(self, preset):
        self.player.set_rate_signed(preset)
        self.player.play()
        self._sync_speed()
        self._update_play_button()

    def _bump_speed(self, direction):
        """Move to the next/previous preset (used by the Up/Down keys)."""
        current = self.player.get_rate_signed()
        nearest = min(range(len(SPEED_PRESETS)),
                      key=lambda i: abs(SPEED_PRESETS[i] - current))
        nxt = max(0, min(len(SPEED_PRESETS) - 1, nearest + direction))
        self._on_speed(SPEED_PRESETS[nxt])

    def _sync_speed(self):
        rate = self.player.get_rate_signed()
        arrow = '◀ REVERSE' if rate < 0 else '▶'
        self.speed_label.setText(f'{arrow}  x{abs(rate):g}')
        if rate in SPEED_PRESETS:
            self.speed_slider.blockSignals(True)
            self.speed_slider.setValue(SPEED_PRESETS.index(rate))
            self.speed_slider.blockSignals(False)

    def _update_play_button(self):
        paused = self.player.is_paused()
        if qta:
            self.btn_play.setIcon(self._icon('play' if paused else 'pause'))
        else:
            self.btn_play.setText('▶' if paused else '⏸')

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
            self._step(-1)
        elif key == Qt.Key_Right:
            self._step(+1)
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
