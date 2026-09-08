#!/usr/bin/env python3
"""Convert a camel-RBQ10 binary log (.rbq10.bin) into a rosbag2 (mcap).

The resulting bag plays back with the existing bag_player + RViz setup
(robot model, TF, odometry) and its scalar signals can be graphed in
PlotJuggler / rqt_plot.

Source format (verified against camel-RBQ10/src/modules/logger):
  - 56-byte header: RBQ_LogFileHeader  {char magic[8]="RBQ10LOG";
                    uint32 schema_version; uint64 started_unix_s;
                    uint32 packet_size; uint8 reserved[32];}
  - N x packet:    RBQ_LogPacket (#pragma pack(1), 733 bytes)

Default signal set (topics):
  /joint_states          sensor_msgs/JointState   pos=mtPos vel=mtVel effort=tau
  /joint_states_desired  sensor_msgs/JointState   ref_mtPos / ref_mtVel / tauRef
  /odom                  nav_msgs/Odometry        basePos+quat, baseVel+angVel
  /tf                    tf2_msgs/TFMessage       odom -> base_link
  /feet                  visualization_msgs/MarkerArray  foot spheres (contact colour)
  /grf                   visualization_msgs/MarkerArray  GRF arrows at feet
  /contact_prob          std_msgs/Float32MultiArray  [Hz*4, Fz*4, Gait*4]
  /timing                std_msgs/Float32MultiArray  [wbcTimeMs, ctrlElapsedMs]
  /fsm_state             std_msgs/Int32

Usage:
  python3 rbq10_log_to_bag.py INPUT.rbq10.bin [-o OUT_DIR] [--stride N]
  # then:  ./run.sh OUT_DIR         (robot playback in RViz)
  #        plotjuggler               (open OUT_DIR/*.mcap for graphs)
"""
import argparse
import os
import struct
import sys

# --- packet layout ---------------------------------------------------------
HEADER_FMT = "<8sIQI32s"          # magic, schema_version, started_unix_s, packet_size, reserved
HEADER_SIZE = struct.calcsize(HEADER_FMT)          # 56

PACKET_FMT = (
    "<"
    "Q"      # t_us
    "6f"     # basePos[3], baseVel[3]
    "10f"    # rpy[3], quat[4], angVel[3]
    "24f"    # mtPos[12], mtVel[12]
    "24f"    # footPos[4][3], footVel[4][3]
    "6f"     # ref_basePos[3], ref_baseVel[3]
    "10f"    # ref_rpy[3], ref_quat[4], ref_angVel[3]
    "24f"    # ref_mtPos[12], ref_mtVel[12]
    "24f"    # ref_footPos[4][3], ref_footVel[4][3]
    "4B"     # gait[4]
    "24f"    # tau[12], tauRef[12]
    "4B"     # contactState[4]
    "12f"    # contactProbHz[4], contactProbFz[4], contactProbGait[4]
    "4B"     # contactEstState[4]
    "12f"    # grf[4][3]
    "2f"     # wbcTimeMs, ctrlElapsedMs
    "B"      # fsmState
)
PACKET_SIZE = struct.calcsize(PACKET_FMT)          # 733

JOINT_NAMES = [
    "joint0_HRR", "joint1_HRP", "joint2_HRK", "joint3_HLR",
    "joint4_HLP", "joint5_HLK", "joint6_FRR", "joint7_FRP",
    "joint8_FRK", "joint9_FLR", "joint10_FLP", "joint11_FLK",
]
WORLD_FRAME = "odom"
BASE_FRAME = "base_link"


def _import_ros():
    """Import ROS message types lazily so --help works without a sourced env."""
    global serialize_message, rosbag2_py
    global JointState, Odometry, TFMessage, TransformStamped
    global Marker, MarkerArray, Float32MultiArray, MultiArrayDimension, Int32
    try:
        from rclpy.serialization import serialize_message
        import rosbag2_py
        from sensor_msgs.msg import JointState
        from nav_msgs.msg import Odometry
        from tf2_msgs.msg import TFMessage
        from geometry_msgs.msg import TransformStamped
        from visualization_msgs.msg import Marker, MarkerArray
        from std_msgs.msg import Float32MultiArray, MultiArrayDimension, Int32
    except ImportError as e:
        sys.exit(f"[rbq10_log_to_bag] ROS 2 not sourced ({e}).\n"
                 f"  source /opt/ros/jazzy/setup.bash  then retry.")


class Cursor:
    """Sequentially pop values from the flat unpacked tuple."""
    def __init__(self, vals):
        self.v = vals
        self.i = 0

    def take(self, n):
        r = self.v[self.i:self.i + n]
        self.i += n
        return r

    def one(self):
        r = self.v[self.i]
        self.i += 1
        return r


def parse_packet(data):
    c = Cursor(struct.unpack(PACKET_FMT, data))
    p = {}
    p["t_us"] = c.one()
    p["basePos"] = c.take(3); p["baseVel"] = c.take(3)
    p["rpy"] = c.take(3); p["quat"] = c.take(4); p["angVel"] = c.take(3)
    p["mtPos"] = c.take(12); p["mtVel"] = c.take(12)
    p["footPos"] = c.take(12); p["footVel"] = c.take(12)
    p["ref_basePos"] = c.take(3); p["ref_baseVel"] = c.take(3)
    p["ref_rpy"] = c.take(3); p["ref_quat"] = c.take(4); p["ref_angVel"] = c.take(3)
    p["ref_mtPos"] = c.take(12); p["ref_mtVel"] = c.take(12)
    p["ref_footPos"] = c.take(12); p["ref_footVel"] = c.take(12)
    p["gait"] = c.take(4)
    p["tau"] = c.take(12); p["tauRef"] = c.take(12)
    p["contactState"] = c.take(4)
    p["cpHz"] = c.take(4); p["cpFz"] = c.take(4); p["cpGait"] = c.take(4)
    p["contactEst"] = c.take(4)
    p["grf"] = c.take(12)
    p["wbc"] = c.one(); p["ctrl"] = c.one()
    p["fsm"] = c.one()
    return p


def _stamp(ns):
    from builtin_interfaces.msg import Time
    return Time(sec=int(ns // 1_000_000_000), nanosec=int(ns % 1_000_000_000))


def _f32ma(values, labels):
    m = Float32MultiArray()
    dim = MultiArrayDimension()
    dim.label = labels
    dim.size = len(values)
    dim.stride = len(values)
    m.layout.dim = [dim]
    m.data = [float(v) for v in values]
    return m


def build_messages(p, ns):
    """Return list of (topic, msg) for one packet."""
    stamp = _stamp(ns)
    out = []

    js = JointState()
    js.header.stamp = stamp
    js.name = JOINT_NAMES
    js.position = [float(v) for v in p["mtPos"]]
    js.velocity = [float(v) for v in p["mtVel"]]
    js.effort = [float(v) for v in p["tau"]]
    out.append(("/joint_states", js))

    jsd = JointState()
    jsd.header.stamp = stamp
    jsd.name = JOINT_NAMES
    jsd.position = [float(v) for v in p["ref_mtPos"]]
    jsd.velocity = [float(v) for v in p["ref_mtVel"]]
    jsd.effort = [float(v) for v in p["tauRef"]]
    out.append(("/joint_states_desired", jsd))

    odom = Odometry()
    odom.header.stamp = stamp
    odom.header.frame_id = WORLD_FRAME
    odom.child_frame_id = BASE_FRAME
    odom.pose.pose.position.x, odom.pose.pose.position.y, odom.pose.pose.position.z = map(float, p["basePos"])
    qx, qy, qz, qw = p["quat"]     # Eigen coeffs() order = (x, y, z, w)
    odom.pose.pose.orientation.x = float(qx)
    odom.pose.pose.orientation.y = float(qy)
    odom.pose.pose.orientation.z = float(qz)
    odom.pose.pose.orientation.w = float(qw)
    odom.twist.twist.linear.x, odom.twist.twist.linear.y, odom.twist.twist.linear.z = map(float, p["baseVel"])
    odom.twist.twist.angular.x, odom.twist.twist.angular.y, odom.twist.twist.angular.z = map(float, p["angVel"])
    out.append(("/odom", odom))

    tf = TFMessage()
    # map -> odom identity (matches the robot's PublishTF; lets an RViz config
    # with Fixed Frame "map" work out of the box).
    t_mo = TransformStamped()
    t_mo.header.stamp = stamp
    t_mo.header.frame_id = "map"
    t_mo.child_frame_id = WORLD_FRAME
    t_mo.transform.rotation.w = 1.0
    # odom -> base_link
    t = TransformStamped()
    t.header.stamp = stamp
    t.header.frame_id = WORLD_FRAME
    t.child_frame_id = BASE_FRAME
    t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = map(float, p["basePos"])
    t.transform.rotation.x = float(qx)
    t.transform.rotation.y = float(qy)
    t.transform.rotation.z = float(qz)
    t.transform.rotation.w = float(qw)
    tf.transforms = [t_mo, t]
    out.append(("/tf", tf))

    feet = MarkerArray()
    grf = MarkerArray()
    for leg in range(4):
        fx, fy, fz = (float(p["footPos"][leg * 3 + k]) for k in range(3))
        in_contact = p["contactState"][leg] != 0

        m = Marker()
        m.header.stamp = stamp
        m.header.frame_id = WORLD_FRAME
        m.ns = "feet"
        m.id = leg
        m.type = Marker.SPHERE
        m.action = Marker.ADD
        m.pose.position.x, m.pose.position.y, m.pose.position.z = fx, fy, fz
        m.pose.orientation.w = 1.0
        m.scale.x = m.scale.y = m.scale.z = 0.05
        m.color.a = 0.9
        m.color.r = 0.0 if in_contact else 1.0
        m.color.g = 1.0 if in_contact else 0.0
        m.color.b = 0.0
        feet.markers.append(m)

        # GRF arrow: foot -> foot + force * scale
        from geometry_msgs.msg import Point
        gx, gy, gz = (float(p["grf"][leg * 3 + k]) for k in range(3))
        a = Marker()
        a.header.stamp = stamp
        a.header.frame_id = WORLD_FRAME
        a.ns = "grf"
        a.id = leg
        a.type = Marker.ARROW
        a.action = Marker.ADD
        a.scale.x = 0.01   # shaft diameter
        a.scale.y = 0.02   # head diameter
        a.scale.z = 0.03   # head length
        a.color.a = 0.9
        a.color.r = 1.0
        a.color.g = 0.6
        a.color.b = 0.0
        s = 0.002          # N -> m arrow scale
        a.points = [Point(x=fx, y=fy, z=fz),
                    Point(x=fx + gx * s, y=fy + gy * s, z=fz + gz * s)]
        grf.markers.append(a)
    out.append(("/feet", feet))
    out.append(("/grf", grf))

    out.append(("/contact_prob",
                _f32ma(list(p["cpHz"]) + list(p["cpFz"]) + list(p["cpGait"]),
                       "Hz0-3,Fz0-3,Gait0-3")))
    out.append(("/timing", _f32ma([p["wbc"], p["ctrl"]], "wbcTimeMs,ctrlElapsedMs")))

    fsm = Int32()
    fsm.data = int(p["fsm"])
    out.append(("/fsm_state", fsm))
    return out


TOPIC_TYPES = {
    "/joint_states": "sensor_msgs/msg/JointState",
    "/joint_states_desired": "sensor_msgs/msg/JointState",
    "/odom": "nav_msgs/msg/Odometry",
    "/tf": "tf2_msgs/msg/TFMessage",
    "/feet": "visualization_msgs/msg/MarkerArray",
    "/grf": "visualization_msgs/msg/MarkerArray",
    "/contact_prob": "std_msgs/msg/Float32MultiArray",
    "/timing": "std_msgs/msg/Float32MultiArray",
    "/fsm_state": "std_msgs/msg/Int32",
}


def _make_topic_metadata(name, type_str):
    """rosbag2_py.TopicMetadata signature varies across distros."""
    try:
        return rosbag2_py.TopicMetadata(
            name=name, type=type_str, serialization_format="cdr")
    except TypeError:
        return rosbag2_py.TopicMetadata(
            0, name, type_str, "cdr")


def _monotonic_timeline(t_us_list, base_ns):
    """Build strictly increasing per-packet timestamps (ns).

    `t_us` is controller uptime and can jump backwards when the controller
    restarts mid-file. Replace any non-positive or pathological step with a
    nominal dt (the median positive step) so playback timing stays sane and
    the sessions are concatenated back-to-back.
    """
    import statistics
    diffs = [b - a for a, b in zip(t_us_list, t_us_list[1:]) if b > a]
    nominal = int(statistics.median(diffs)) if diffs else 20000  # us
    cap = nominal * 10
    ns = [0] * len(t_us_list)
    elapsed = 0
    ns[0] = base_ns
    for k in range(1, len(t_us_list)):
        dt = t_us_list[k] - t_us_list[k - 1]
        if dt <= 0 or dt > cap:
            dt = nominal
        elapsed += dt
        ns[k] = base_ns + elapsed * 1000
    return ns, nominal


def convert(in_path, out_dir, storage_id, stride):
    with open(in_path, "rb") as f:
        raw = f.read()

    if len(raw) < HEADER_SIZE:
        sys.exit(f"[rbq10_log_to_bag] file too small / empty: {in_path}")

    magic, schema, started_unix_s, packet_size, _ = struct.unpack(
        HEADER_FMT, raw[:HEADER_SIZE])
    if magic != b"RBQ10LOG":
        sys.exit(f"[rbq10_log_to_bag] bad magic {magic!r} (not an RBQ10 log): {in_path}")
    if packet_size != PACKET_SIZE:
        sys.exit(f"[rbq10_log_to_bag] packet_size {packet_size} != expected {PACKET_SIZE} "
                 f"(schema v{schema}); this parser matches schema v2. Aborting.")

    body = raw[HEADER_SIZE:]
    n_packets = len(body) // PACKET_SIZE
    if n_packets == 0:
        sys.exit(f"[rbq10_log_to_bag] header-only log, no packets: {in_path}")

    base_ns = started_unix_s * 1_000_000_000
    t_us_list = [struct.unpack_from("<Q", body, k * PACKET_SIZE)[0]
                 for k in range(n_packets)]
    ns_list, nominal = _monotonic_timeline(t_us_list, base_ns)
    n_resets = sum(1 for a, b in zip(t_us_list, t_us_list[1:]) if b < a)

    writer = rosbag2_py.SequentialWriter()
    writer.open(
        rosbag2_py.StorageOptions(uri=out_dir, storage_id=storage_id),
        rosbag2_py.ConverterOptions(
            input_serialization_format="cdr",
            output_serialization_format="cdr"))
    for name, type_str in TOPIC_TYPES.items():
        writer.create_topic(_make_topic_metadata(name, type_str))

    written = skipped = 0
    for k in range(0, n_packets, stride):
        # Drop the trailing/flush artifact packets (uptime 0, not the first).
        if k > 0 and t_us_list[k] == 0:
            skipped += 1
            continue
        off = k * PACKET_SIZE
        p = parse_packet(body[off:off + PACKET_SIZE])
        ns = ns_list[k]
        for topic, msg in build_messages(p, ns):
            writer.write(topic, serialize_message(msg), ns)
        written += 1

    del writer  # flush/close
    dur = (ns_list[-1] - ns_list[0]) / 1e9
    extra = f", {n_resets} restart(s) concatenated" if n_resets else ""
    extra += f", {skipped} artifact packet(s) skipped" if skipped else ""
    print(f"[rbq10_log_to_bag] {os.path.basename(in_path)}: "
          f"{written}/{n_packets} packets (stride {stride}, ~{1e6/nominal:.0f}Hz), "
          f"{dur:.1f}s -> {out_dir}{extra}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="path to a .rbq10.bin log file")
    ap.add_argument("-o", "--output", help="output bag directory "
                    "(default: <input without .rbq10.bin>_bag)")
    ap.add_argument("--storage", default="mcap", choices=["mcap", "sqlite3"],
                    help="rosbag2 storage id (default: mcap)")
    ap.add_argument("--stride", type=int, default=1,
                    help="keep every Nth packet (default 1; use >1 to downsample huge logs)")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"[rbq10_log_to_bag] no such file: {args.input}")
    if args.stride < 1:
        sys.exit("[rbq10_log_to_bag] --stride must be >= 1")

    out_dir = args.output
    if out_dir is None:
        base = args.input
        for ext in (".rbq10.bin", ".bin"):
            if base.endswith(ext):
                base = base[:-len(ext)]
                break
        out_dir = base + "_bag"
    if os.path.exists(out_dir):
        sys.exit(f"[rbq10_log_to_bag] output already exists (rosbag2 needs a fresh dir): {out_dir}")

    _import_ros()
    convert(args.input, out_dir, args.storage, args.stride)


if __name__ == "__main__":
    main()
