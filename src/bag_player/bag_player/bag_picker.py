"""Startup dialog that lets the user choose which bag (log) folder to play.

``run.sh`` invokes this before ``ros2 launch``: it scans a search root for
rosbag2 bags (any directory containing a ``metadata.yaml``), shows them with
duration / size / record time, and writes the chosen path back so the launch
file can be started with it.

Usage:
    python3 -m bag_player.bag_picker --root ~/ros2bag --out /tmp/sel
    ros2 run bag_player bag_picker --root ~/ros2bag

The selection is printed to stdout (and written to ``--out`` when given) as a
single tab-separated line::

    /path/to/bag<TAB>mcap

Exit code 0 = a bag was chosen, 1 = cancelled / nothing found.
"""

import argparse
import os
import sys
import time

# Depth (below the search root) at which we stop looking for metadata.yaml.
# Bags are usually  <root>/<session>/<bag_records>/metadata.yaml.
MAX_SCAN_DEPTH = 4


# ---------------------------------------------------------------------- #
# Bag discovery
# ---------------------------------------------------------------------- #
class BagInfo:
    """What we can tell about a bag directory without opening it in rosbag2."""

    def __init__(self, path):
        self.path = path
        self.storage_id = 'mcap'
        self.duration_s = None
        self.message_count = None
        self.topic_count = None
        self.start_epoch = None
        self.size_bytes = None

    def label(self, root=None):
        if root:
            try:
                rel = os.path.relpath(self.path, root)
                if not rel.startswith('..'):
                    return rel
            except ValueError:
                pass
        return self.path


def _dir_size(path):
    """Total size of a bag directory (metadata + storage files)."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def read_bag_info(path):
    """Parse ``metadata.yaml``. Missing / unparsable fields stay ``None``."""
    info = BagInfo(os.path.abspath(path))
    try:
        import yaml
        with open(os.path.join(path, 'metadata.yaml'), 'r') as f:
            doc = yaml.safe_load(f) or {}
        meta = doc.get('rosbag2_bagfile_information') or {}
        info.storage_id = meta.get('storage_identifier') or 'mcap'
        duration = (meta.get('duration') or {}).get('nanoseconds')
        if duration is not None:
            info.duration_s = float(duration) / 1e9
        start = (meta.get('starting_time') or {}).get('nanoseconds_since_epoch')
        if start:
            info.start_epoch = float(start) / 1e9
        info.message_count = meta.get('message_count')
        info.topic_count = len(meta.get('topics_with_message_count') or [])
    except Exception:
        # An unparsable metadata.yaml is still a bag the user may want to try.
        pass
    info.size_bytes = _dir_size(path)
    return info


def find_bags(root, max_depth=MAX_SCAN_DEPTH):
    """Return every bag directory under *root*, newest recording first."""
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        return []

    base_depth = root.rstrip(os.sep).count(os.sep)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        if 'metadata.yaml' in filenames:
            found.append(read_bag_info(dirpath))
            dirnames[:] = []            # a bag never contains another bag
            continue
        if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in sorted(dirnames) if not d.startswith('.')]

    found.sort(key=lambda b: (b.start_epoch or 0.0), reverse=True)
    return found


def resolve_bag_dir(path):
    """Accept a folder that *contains* a bag one level down, too.

    Returns the directory holding ``metadata.yaml``, or ``None``.
    """
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(os.path.join(path, 'metadata.yaml')):
        return path
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name):
            if entry.is_dir() and os.path.isfile(
                    os.path.join(entry.path, 'metadata.yaml')):
                return entry.path
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------- #
# Formatting helpers
# ---------------------------------------------------------------------- #
def fmt_duration(seconds):
    if seconds is None:
        return '?'
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def fmt_size(nbytes):
    if nbytes is None:
        return '?'
    value = float(nbytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024.0 or unit == 'TB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{int(value)} B'
        value /= 1024.0


def fmt_time(epoch):
    if not epoch:
        return '?'
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(epoch))


def fmt_count(n):
    return '?' if n is None else f'{n:,}'


# ---------------------------------------------------------------------- #
# Qt dialog
# ---------------------------------------------------------------------- #
def _build_dialog_classes():
    """Import PyQt5 lazily so the text fallback works without it."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QDialog,
        QFileDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )

    class SortItem(QTableWidgetItem):
        """Table cell that sorts on a numeric key instead of its text."""

        def __init__(self, text, key=None):
            super().__init__(text)
            self.key = key
            self.setFlags(self.flags() & ~Qt.ItemIsEditable)

        def __lt__(self, other):
            if isinstance(other, SortItem) and self.key is not None \
                    and other.key is not None:
                return self.key < other.key
            return super().__lt__(other)

    COLUMNS = ('Bag folder', 'Duration', 'Messages', 'Topics',
               'Storage', 'Size', 'Recorded')

    class BagPickerDialog(QDialog):
        def __init__(self, root, initial=None):
            super().__init__()
            self.root = os.path.abspath(os.path.expanduser(root))
            self.initial = initial
            self.selected = None
            self.bags = []

            self.setWindowTitle('Select bag (log) folder')
            self.resize(920, 480)
            self._build_ui()
            self._rescan()

        # -------------------------------------------------------------- #
        def _build_ui(self):
            root_layout = QVBoxLayout(self)

            # --- search root -------------------------------------------
            row = QHBoxLayout()
            row.addWidget(QLabel('Search in:'))
            self.root_edit = QLineEdit(self.root)
            self.root_edit.returnPressed.connect(self._rescan)
            row.addWidget(self.root_edit, 1)
            btn_root = QPushButton('…')
            btn_root.setFixedWidth(36)
            btn_root.clicked.connect(self._choose_root)
            row.addWidget(btn_root)
            btn_rescan = QPushButton('Rescan')
            btn_rescan.clicked.connect(self._rescan)
            row.addWidget(btn_rescan)
            root_layout.addLayout(row)

            # --- bag table ---------------------------------------------
            self.table = QTableWidget(0, len(COLUMNS))
            self.table.setHorizontalHeaderLabels(COLUMNS)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.verticalHeader().setVisible(False)
            self.table.setSortingEnabled(True)
            self.table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.Stretch)
            self.table.itemSelectionChanged.connect(self._sync_path_label)
            self.table.itemDoubleClicked.connect(lambda _item: self._accept())
            root_layout.addWidget(self.table, 1)

            self.path_label = QLabel('')
            self.path_label.setStyleSheet('color: gray; font-size: 11px;')
            self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            root_layout.addWidget(self.path_label)

            # --- buttons ------------------------------------------------
            buttons = QHBoxLayout()
            btn_browse = QPushButton('Browse folder…')
            btn_browse.clicked.connect(self._browse_bag)
            buttons.addWidget(btn_browse)
            buttons.addStretch(1)
            btn_cancel = QPushButton('Cancel')
            btn_cancel.clicked.connect(self.reject)
            buttons.addWidget(btn_cancel)
            self.btn_open = QPushButton('Play')
            self.btn_open.setDefault(True)
            self.btn_open.clicked.connect(self._accept)
            buttons.addWidget(self.btn_open)
            root_layout.addLayout(buttons)

        # -------------------------------------------------------------- #
        # Scanning / population
        # -------------------------------------------------------------- #
        def _choose_root(self):
            path = QFileDialog.getExistingDirectory(
                self, 'Folder to scan for bags', self.root_edit.text())
            if path:
                self.root_edit.setText(path)
                self._rescan()

        def _rescan(self):
            self.root = os.path.abspath(
                os.path.expanduser(self.root_edit.text().strip() or '.'))
            self.bags = find_bags(self.root)
            self._populate()

        def _populate(self):
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(self.bags))
            for row, bag in enumerate(self.bags):
                cells = (
                    SortItem(bag.label(self.root), None),
                    SortItem(fmt_duration(bag.duration_s), bag.duration_s or 0),
                    SortItem(fmt_count(bag.message_count), bag.message_count or 0),
                    SortItem(fmt_count(bag.topic_count), bag.topic_count or 0),
                    SortItem(bag.storage_id, None),
                    SortItem(fmt_size(bag.size_bytes), bag.size_bytes or 0),
                    SortItem(fmt_time(bag.start_epoch), bag.start_epoch or 0),
                )
                # Keep the path on the row so sorting cannot desync the table
                # order from ``self.bags``.
                cells[0].setData(Qt.UserRole, bag.path)
                cells[0].setToolTip(bag.path)
                for col, item in enumerate(cells):
                    if col:
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(row, col, item)
            self.table.setSortingEnabled(True)
            # Newest recording first — and make the header indicator agree.
            self.table.sortByColumn(len(COLUMNS) - 1, Qt.DescendingOrder)
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.Stretch)

            if not self.bags:
                self.path_label.setText(
                    f'No bag (metadata.yaml) found under {self.root} — '
                    'use "Browse folder…".')
                self.btn_open.setEnabled(False)
                return

            self.btn_open.setEnabled(True)
            self._select_default()

        def _select_default(self):
            """Preselect the bag from --initial, else the newest one."""
            wanted = os.path.abspath(os.path.expanduser(self.initial)) \
                if self.initial else None
            target = 0
            for row in range(self.table.rowCount()):
                bag = self._bag_at(row)
                if bag is not None and bag.path == wanted:
                    target = row
                    break
            self.table.selectRow(target)
            self._sync_path_label()

        def _bag_at(self, row):
            """Map a *view* row back to its BagInfo (the table can be sorted)."""
            item = self.table.item(row, 0)
            if item is None:
                return None
            path = item.data(Qt.UserRole)
            return next((b for b in self.bags if b.path == path), None)

        def _current_bag(self):
            rows = self.table.selectionModel().selectedRows() \
                if self.table.selectionModel() else []
            if not rows:
                return None
            return self._bag_at(rows[0].row())

        def _sync_path_label(self):
            bag = self._current_bag()
            self.path_label.setText(bag.path if bag else '')

        # -------------------------------------------------------------- #
        # Selection
        # -------------------------------------------------------------- #
        def _browse_bag(self):
            path = QFileDialog.getExistingDirectory(
                self, 'Select a bag folder (contains metadata.yaml)',
                self.root_edit.text())
            if not path:
                return
            bag_dir = resolve_bag_dir(path)
            if not bag_dir:
                QMessageBox.warning(
                    self, 'Not a bag folder',
                    f'No metadata.yaml in:\n{path}\n\n'
                    'Pick the directory that contains metadata.yaml.')
                return
            self.selected = read_bag_info(bag_dir)
            self.accept()

        def _accept(self):
            bag = self._current_bag()
            if bag is None:
                return
            self.selected = bag
            self.accept()

    return BagPickerDialog


def pick_with_gui(root, initial=None):
    """Show the dialog. Returns a :class:`BagInfo` or ``None`` if cancelled."""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    dialog = _build_dialog_classes()(root, initial=initial)
    if dialog.exec_() and dialog.selected is not None:
        selected = dialog.selected
    else:
        selected = None
    app.processEvents()
    return selected


# ---------------------------------------------------------------------- #
# Terminal fallback (no display / no PyQt5)
# ---------------------------------------------------------------------- #
def pick_with_text(root, initial=None):
    bags = find_bags(root)
    if not bags:
        print(f'[bag_picker] No bag found under {root}', file=sys.stderr)
        return None

    print(f'Bags under {root}:', file=sys.stderr)
    for i, bag in enumerate(bags, 1):
        mark = '*' if initial and os.path.abspath(
            os.path.expanduser(initial)) == bag.path else ' '
        print(f' {mark}{i:2d}) {bag.label(root)}  '
              f'[{fmt_duration(bag.duration_s)}, {fmt_size(bag.size_bytes)}, '
              f'{fmt_time(bag.start_epoch)}]', file=sys.stderr)
    try:
        answer = input('Select number (empty = 1, q = quit): ').strip()
    except EOFError:
        return None
    if answer.lower().startswith('q'):
        return None
    if not answer:
        return bags[0]
    try:
        return bags[int(answer) - 1]
    except (ValueError, IndexError):
        print('[bag_picker] Invalid selection.', file=sys.stderr)
        return None


# ---------------------------------------------------------------------- #
# Entry point
# ---------------------------------------------------------------------- #
def _has_display():
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog='bag_picker',
        description='Choose a rosbag2 bag (log) folder to play.')
    parser.add_argument('--root', '-r', default='~/ros2bag',
                        help='Directory to scan for bags (default: ~/ros2bag).')
    parser.add_argument('--initial', '-i', default=None,
                        help='Bag path to preselect.')
    parser.add_argument('--out', '-o', default=None,
                        help='Write "<path>\\t<storage_id>" to this file.')
    parser.add_argument('--no-gui', action='store_true',
                        help='Always use the terminal list instead of the dialog.')
    args, _ = parser.parse_known_args(argv)

    use_gui = not args.no_gui and _has_display()
    bag = None
    if use_gui:
        try:
            bag = pick_with_gui(args.root, args.initial)
        except ImportError as exc:
            print(f'[bag_picker] PyQt5 unavailable ({exc}); '
                  'falling back to the terminal list.', file=sys.stderr)
            use_gui = False
    if not use_gui and bag is None:
        bag = pick_with_text(args.root, args.initial)

    if bag is None:
        return 1

    line = f'{bag.path}\t{bag.storage_id}'
    if args.out:
        with open(args.out, 'w') as f:
            f.write(line + '\n')
    print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
