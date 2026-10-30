"""Playback engine for ROS 2 bags with video-player semantics.

The engine opens a bag with ``rosbag2_py.SequentialReader`` and republishes the
recorded messages on their original topics, driven by a virtual *bag clock* that
the user can move forward, backward, faster, slower, or jump to any point.

Key facts that make this possible (verified on rosbag2 / Jazzy + MCAP):

* ``SequentialReader.seek(timestamp_ns)`` repositions to an arbitrary time.
* ``ReadOrder(reverse=True)`` makes ``read_next()`` yield messages in *decreasing*
  timestamp order, which gives true reverse playback without re-indexing.

A ``/clock`` topic is published so that downstream consumers running with
``use_sim_time:=true`` (e.g. RViz2) follow the same virtual time, including jumps
and reverse motion.
"""

import threading
import time

import rosbag2_py
from builtin_interfaces.msg import Time as TimeMsg
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from rclpy.serialization import deserialize_message
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import CompressedImage
from rosidl_runtime_py.utilities import get_message

NS_PER_S = 1_000_000_000

# Hard caps so a stall (or a huge jump) never spirals into an unbounded
# publish burst inside a single engine tick.
MAX_ADVANCE_NS = NS_PER_S          # never advance more than 1 s of bag time per tick
MAX_MSGS_PER_TICK = 800            # drop the rest if a tick would publish more than this
BACKWARD_CLOCK_PERIOD_S = 0.25     # min wall time between backward /clock steps (each one resets every RViz display)

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _fix_compressed_format(msg):
    """Bare 'png' 16-bit greyscale -> '16UC1; png' so image_transport decodes depth correctly."""
    if ';' in msg.format or len(msg.data) < 26 or bytes(msg.data[:8]) != PNG_MAGIC:
        return
    if msg.data[24] == 16 and msg.data[25] == 0:  # IHDR bit depth, colour type
        msg.format = '16UC1; png'


class PlayerCore(Node):
    """rclpy node that owns the bag reader, the publishers and the play thread."""

    def __init__(self, bag_uri, storage_id='mcap', node_name='bag_player'):
        super().__init__(node_name)
        self.bag_uri = bag_uri
        self.storage_id = storage_id

        self._lock = threading.Lock()

        # --- open bag, read metadata -------------------------------------
        self._reader = self._make_reader()
        meta = self._reader.get_metadata()
        self.start_ns = int(meta.starting_time.nanoseconds)
        self.duration_ns = int(meta.duration.nanoseconds)
        self.end_ns = self.start_ns + self.duration_ns
        self.message_count = int(meta.message_count)
        self.topic_counts = {t.topic_metadata.name: int(t.message_count)
                             for t in meta.topics_with_message_count}

        # --- build publishers + message classes --------------------------
        self.topic_types = {}
        self._pubs = {}
        self._msg_classes = {}
        self._sync_publishers({info.name: info.type
                               for info in self._reader.get_all_topics_and_types()})

        clock_qos = QoSProfile(
            depth=10,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE,
        )
        self._clock_pub = self.create_publisher(Clock, '/clock', clock_qos)

        # --- mutable playback state (guarded by _lock) -------------------
        self._bag_time_ns = self.start_ns
        self._rate = 1.0           # speed magnitude (>0)
        self._dir = 1              # +1 forward, -1 reverse
        self._paused = True
        self._loop = False
        self._enabled = set(self._pubs.keys())
        self._seek_req = None      # absolute bag ns requested by the GUI
        self._step_req = None      # (direction, frame topic or None) to step while paused
        self._load_req = None      # (bag_uri, storage_id) requested by the GUI
        self._load_error = None    # message of the last failed load, for the GUI
        self._bag_generation = 0   # bumped on every successful bag (re)load
        self._running = True

        # --- reader cursor state (engine thread only) --------------------
        self._reader_dir = 1
        self._pending = None       # one-message lookahead: (topic, data, t_ns)
        self._frame_ns = None      # bag time of the frame the last step landed on
        self._clock_ns = 0
        self._clock_back_wall = 0.0

        # Latch static transforms so RViz can resolve frames after any seek.
        self._publish_static_once()

        # Position the cursor at the very start, paused.
        self._reposition(self.start_ns, 1)
        self._publish_clock(self.start_ns)

        self._thread = threading.Thread(target=self._run, name='bag-play', daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ #
    # Setup helpers
    # ------------------------------------------------------------------ #
    def _make_reader(self, bag_uri=None, storage_id=None):
        reader = rosbag2_py.SequentialReader()
        storage = rosbag2_py.StorageOptions(uri=bag_uri or self.bag_uri,
                                            storage_id=storage_id or self.storage_id)
        converter = rosbag2_py.ConverterOptions('', '')
        reader.open(storage, converter)
        return reader

    def _sync_publishers(self, new_types):
        """Make the publishers match ``new_types``, reusing unchanged topics."""
        for name in list(self._pubs):
            if new_types.get(name) == self.topic_types.get(name):
                continue
            self._msg_classes.pop(name, None)
            self.destroy_publisher(self._pubs.pop(name))
        for name, type_str in sorted(new_types.items()):
            if name in self._pubs:
                continue
            try:
                cls = get_message(type_str)
            except Exception as exc:  # unknown message type -> skip the topic
                self.get_logger().warn(f'No type support for {name} ({type_str}): {exc}')
                continue
            self._msg_classes[name] = cls
            self._pubs[name] = self.create_publisher(cls, name, self._qos_for(name))
        self.topic_types = new_types

    def _qos_for(self, topic):
        # tf_static is conventionally latched; everything else is volatile.
        durability = (QoSDurabilityPolicy.TRANSIENT_LOCAL
                      if topic == '/tf_static'
                      else QoSDurabilityPolicy.VOLATILE)
        return QoSProfile(
            depth=10,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=durability,
        )

    def _publish_static_once(self):
        """Read and latch every /tf_static message so frames stay resolvable."""
        if '/tf_static' not in self._pubs:
            return
        try:
            reader = self._make_reader()
            reader.set_filter(rosbag2_py.StorageFilter(topics=['/tf_static']))
            cls = self._msg_classes['/tf_static']
            while reader.has_next():
                topic, data, _ = reader.read_next()
                if topic == '/tf_static':
                    self._pubs['/tf_static'].publish(deserialize_message(data, cls))
            del reader
        except Exception as exc:
            self.get_logger().warn(f'Could not latch /tf_static: {exc}')

    # ------------------------------------------------------------------ #
    # Reader cursor management (engine thread only)
    # ------------------------------------------------------------------ #
    def _reposition(self, t_ns, direction):
        """Seek the reader to ``t_ns`` reading in ``direction`` (+1/-1)."""
        order = rosbag2_py.ReadOrder()
        order.reverse = direction < 0
        self._reader.set_read_order(order)
        t_ns = max(self.start_ns, min(self.end_ns, int(t_ns)))
        self._reader.seek(t_ns)
        self._reader_dir = direction
        self._pending = None

    def _ensure_pending(self):
        if self._pending is None and self._reader.has_next():
            self._pending = self._reader.read_next()
        return self._pending

    def _do_load(self, bag_uri, storage_id):
        """Switch to another bag (play thread only). Keeps the old one on failure."""
        try:
            reader = self._make_reader(bag_uri, storage_id)
            meta = reader.get_metadata()
        except Exception as exc:
            self.get_logger().error(f'Could not open bag {bag_uri}: {exc}')
            with self._lock:
                self._load_error = f'{bag_uri}\n\n{exc}'
            return

        self._reader = reader
        self.bag_uri = bag_uri
        self.storage_id = storage_id
        self.start_ns = int(meta.starting_time.nanoseconds)
        self.duration_ns = int(meta.duration.nanoseconds)
        self.end_ns = self.start_ns + self.duration_ns
        self.message_count = int(meta.message_count)
        self.topic_counts = {t.topic_metadata.name: int(t.message_count)
                             for t in meta.topics_with_message_count}
        self._sync_publishers({info.name: info.type
                               for info in reader.get_all_topics_and_types()})

        with self._lock:
            self._bag_time_ns = self.start_ns
            self._enabled = set(self._pubs.keys())
            self._paused = True
            self._seek_req = None
            self._step_req = None
            self._bag_generation += 1

        # Same startup sequence as __init__: latch the new statics and park at
        # the start. When the new bag lies earlier in time, the /clock jump
        # also makes RViz drop everything cached from the previous bag.
        self._publish_static_once()
        self._reposition(self.start_ns, 1)
        self._publish_clock(self.start_ns)
        self.get_logger().info(
            f'Switched to {bag_uri} ({self.message_count} messages, '
            f'{self.duration_ns / 1e9:.1f} s)')

    def _publish_msg(self, topic, data):
        cls = self._msg_classes.get(topic)
        pub = self._pubs.get(topic)
        if cls is None or pub is None:
            return
        try:
            msg = deserialize_message(data, cls)
            if cls is CompressedImage:
                _fix_compressed_format(msg)
            pub.publish(msg)
        except Exception as exc:
            self.get_logger().warn(f'Publish failed on {topic}: {exc}',
                                   throttle_duration_sec=5.0)

    def _publish_range(self, target_ns, direction, enabled):
        """Publish every pending message between the cursor and ``target_ns``."""
        published = 0
        while True:
            pending = self._ensure_pending()
            if pending is None:
                break
            topic, data, t = pending
            if direction > 0 and t > target_ns:
                break
            if direction < 0 and t < target_ns:
                break
            self._pending = None  # consume
            if topic in enabled:
                if published >= MAX_MSGS_PER_TICK:
                    continue  # keep draining timestamps, but stop publishing this tick
                self._publish_msg(topic, data)
                published += 1

    def _step_frame(self, bag_time, direction, frame_topic, enabled):
        """Publish up to and including the next ``frame_topic`` message; return its time."""
        shown = bag_time == self._frame_ns
        while True:
            pending = self._ensure_pending()
            if pending is None:
                return self.end_ns if direction > 0 else self.start_ns
            topic, data, t = pending
            self._pending = None
            if topic in enabled:
                self._publish_msg(topic, data)
            ahead = (t - bag_time) * direction
            if (ahead > 0 or (ahead == 0 and not shown)) and frame_topic in (None, topic):
                self._frame_ns = t
                return t

    def _publish_clock(self, t_ns):
        t_ns = int(t_ns)
        if t_ns < self._clock_ns:
            now = time.monotonic()
            if now - self._clock_back_wall < BACKWARD_CLOCK_PERIOD_S:
                return
            self._clock_back_wall = now
        self._clock_ns = t_ns
        self._clock_pub.publish(Clock(clock=TimeMsg(sec=t_ns // NS_PER_S,
                                                    nanosec=t_ns % NS_PER_S)))

    # ------------------------------------------------------------------ #
    # The play thread
    # ------------------------------------------------------------------ #
    def _run(self):
        prev = time.monotonic()
        while self._running:
            now = time.monotonic()
            dt = now - prev
            prev = now

            with self._lock:
                paused = self._paused
                rate = self._rate
                direction = self._dir
                loop = self._loop
                seek_req = self._seek_req
                step_req = self._step_req
                load_req = self._load_req
                self._seek_req = None
                self._step_req = None
                self._load_req = None
                enabled = frozenset(self._enabled)
                bag_time = self._bag_time_ns

            # --- switch to another bag -----------------------------------
            if load_req is not None:
                self._do_load(*load_req)
                continue

            # --- explicit seek (scrub / click) ---------------------------
            if seek_req is not None:
                bag_time = max(self.start_ns, min(self.end_ns, int(seek_req)))
                self._reposition(bag_time, direction)
                self._publish_clock(bag_time)
                with self._lock:
                    self._bag_time_ns = bag_time

            # --- single frame step while paused --------------------------
            if step_req is not None:
                step_dir, frame_topic = step_req
                if self._reader_dir != step_dir:
                    self._reposition(bag_time, step_dir)
                bag_time = self._step_frame(bag_time, step_dir, frame_topic, enabled)
                self._publish_clock(bag_time)
                with self._lock:
                    self._bag_time_ns = bag_time
                continue

            # --- paused: keep the clock alive so RViz time holds steady ---
            if paused or rate == 0.0:
                self._publish_clock(bag_time)
                time.sleep(0.02)
                continue

            # --- keep the reader pointing the right way ------------------
            if self._reader_dir != direction:
                self._reposition(bag_time, direction)

            # --- advance the virtual clock and publish -------------------
            advance = int(direction * rate * dt * NS_PER_S)
            if advance > MAX_ADVANCE_NS:
                advance = MAX_ADVANCE_NS
            elif advance < -MAX_ADVANCE_NS:
                advance = -MAX_ADVANCE_NS

            target = bag_time + advance
            hit_boundary = False
            if target >= self.end_ns:
                target, hit_boundary = self.end_ns, True
            elif target <= self.start_ns:
                target, hit_boundary = self.start_ns, True

            self._publish_range(target, direction, enabled)
            bag_time = target
            self._publish_clock(bag_time)

            with self._lock:
                self._bag_time_ns = bag_time
                if hit_boundary:
                    if loop:
                        self._seek_req = self.start_ns if direction > 0 else self.end_ns
                    else:
                        self._paused = True

            time.sleep(0.002)

    # ------------------------------------------------------------------ #
    # Public API (thread-safe) — called from the GUI thread
    # ------------------------------------------------------------------ #
    def play(self):
        with self._lock:
            self._paused = False

    def pause(self):
        with self._lock:
            self._paused = True

    def toggle(self):
        with self._lock:
            self._paused = not self._paused
            return self._paused

    def is_paused(self):
        with self._lock:
            return self._paused

    def set_rate_signed(self, signed_rate):
        """Set speed and direction in one call. Negative = reverse, 0 = pause."""
        with self._lock:
            if signed_rate == 0:
                self._paused = True
                return
            self._dir = 1 if signed_rate > 0 else -1
            self._rate = abs(float(signed_rate))

    def get_rate_signed(self):
        with self._lock:
            return self._dir * self._rate

    def seek_fraction(self, fraction):
        fraction = max(0.0, min(1.0, float(fraction)))
        with self._lock:
            self._seek_req = self.start_ns + int(fraction * self.duration_ns)

    def seek_seconds(self, seconds):
        with self._lock:
            self._seek_req = self.start_ns + int(max(0.0, seconds) * NS_PER_S)

    def step_frame(self, direction, topic=None):
        """Move to the next (+1) / previous (-1) message of ``topic`` (any if None). Pauses."""
        with self._lock:
            self._paused = True
            self._step_req = (1 if direction > 0 else -1, topic)

    def set_loop(self, enabled):
        with self._lock:
            self._loop = bool(enabled)

    def set_topic_enabled(self, topic, enabled):
        with self._lock:
            if enabled:
                self._enabled.add(topic)
            else:
                self._enabled.discard(topic)

    def request_load(self, bag_uri, storage_id='mcap'):
        """Ask the play thread to switch to another bag (asynchronous).

        Success bumps ``get_bag_generation()``; failure keeps the current bag
        and leaves a message for ``take_load_error()``.
        """
        with self._lock:
            self._paused = True
            self._load_req = (bag_uri, storage_id)

    def get_bag_generation(self):
        with self._lock:
            return self._bag_generation

    def take_load_error(self):
        """Return-and-clear the last failed load message (None when none)."""
        with self._lock:
            error, self._load_error = self._load_error, None
            return error

    def get_position(self):
        """Return (fraction 0..1, elapsed_seconds, total_seconds)."""
        with self._lock:
            t = self._bag_time_ns
        elapsed = (t - self.start_ns) / NS_PER_S
        total = self.duration_ns / NS_PER_S
        frac = (t - self.start_ns) / self.duration_ns if self.duration_ns else 0.0
        # A bag switch updates start/duration and the clock non-atomically, so
        # clamp the one refresh tick that may see a mixed snapshot.
        return max(0.0, min(1.0, frac)), elapsed, total

    def shutdown(self):
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)
