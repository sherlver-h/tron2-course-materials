"""TRON2 teaching client: official WebSocket packets.

Default: local simulator 127.0.0.1 only. A real robot is reachable only through
an explicit, validated robot profile (load_robot_profile / --robot).

MoveP frame: origin at the bottom-plane center of the robot base; end frame
at the last arm joint center. Pose order is XYZ + WXYZ, not ROS XYZW.
This client does not convert the course MuJoCo base_Link into that frame.
"""
from __future__ import annotations

import json
import math
import socket
import threading
import time
import uuid
from pathlib import Path

ARM_NAMES = [f"{joint}_{side}_Joint" for side in ("L", "R") for joint in (
    "proximal_pitch", "proximal_roll", "proximal_yaw", "elbow",
    "wrist_yaw", "wrist_pitch", "wrist_roll")]
HEAD_NAMES = ["head_pitch_Joint", "head_yaw_Joint"]
# Firmware 2.1.24 answers request_get_joint_state with names=[] and 16 values.
# The SDK guide documents MoveJ order (left top->bottom, right top->bottom) but
# not the state order; this layout is an assumption until checked joint by joint.
STATE_ORDERS = {"left7_right7_head2": ARM_NAMES + HEAD_NAMES}
# Conservative intersection: published SDK ranges, model and observed controller.
# The 2026-10-24 controller log narrows shoulder roll to L >= 0 and R <= 0.
# These are simulation checks, not a substitute for an actual device profile.
LOWER = [-3.14159, 0., -3.66519, -2.61799, -1.7453, -.7505, -.7505,
         -3.14159, -2.96706, -1.4835, -2.61799, -1.3963, -.7505, -.7505]
UPPER = [2.6005, 2.96706, 1.4835, .2618, 1.39626, .7505, .7505,
         2.6005, 0., 3.66519, .2618, 1.7453, .7505, .7505]
# TRON 2 user manual, dual-arm joint table (mechanical limits, degrees). A robot
# profile may only narrow these; they are never used as command targets.
MANUAL_LOWER = [math.radians(d) for d in (-180, -15, -210, -150, -100, -43, -43,
                                          -180, -170, -85, -150, -80, -43, -43)]
MANUAL_UPPER = [math.radians(d) for d in (149, 170, 85, 30, 80, 43, 43,
                                          149, 15, 210, 30, 100, 43, 43)]
MANUAL_ROUNDING = 1e-3  # the manual's rad column is rounded to 3 decimals
# SDK guide: debug real robot with single small moves and time >= 5 s.
HARDWARE_MIN_DURATION = 5.
HARDWARE_MAX_JOINT_DELTA = .1
HARDWARE_MAX_POSE_DELTA = .01


class ProtocolError(RuntimeError):
    pass


def load_robot_profile(path, require_names=True):
    """Validate a real-robot profile; same keys as device_profile.template.json."""
    p = json.loads(Path(path).read_text(encoding="utf-8"))
    url = str(p.get("url", ""))
    if not url.startswith("ws://") or "FILL" in url.upper():
        raise ValueError("profile.url must be ws://<robot ip>:<port>, e.g. ws://10.192.1.2:5000")
    host, _, port = url[5:].rstrip("/").partition(":")
    if not host or not port.isdigit():
        raise ValueError("profile.url needs explicit host and port")
    accid = str(p.get("accid") or "")
    if not accid or "FILL" in accid.upper() or "DRYRUN" in accid.upper():
        raise ValueError("profile.accid: fill the SN shown at http://<robot ip>:8080 robot info")
    if not p.get("robot_model") or not p.get("firmware_version"):
        raise ValueError("profile: record robot_model and firmware_version")
    if p.get("mode") != "high_level" or p.get("mode_confirmed") is not True:
        raise ValueError("profile: switch remote to upper-level developer mode, then set "
                         "mode=high_level and mode_confirmed=true")
    names = p.get("arm_state_names") or []
    state_order = p.get("state_order")
    if state_order is not None:
        if state_order not in STATE_ORDERS:
            raise ValueError(f"profile.state_order must be one of {sorted(STATE_ORDERS)}")
        if names:
            raise ValueError("profile: use either arm_state_names or state_order, not both")
        names = ARM_NAMES
    if names or require_names:
        if len(names) != 14 or len(set(names)) != 14 or not all(isinstance(n, str) and n and "FILL" not in n
                                                               for n in names):
            raise ValueError("profile.arm_state_names: 14 unique names from a real query "
                             "(left 7 then right 7)")
    limits = p.get("joint_limits_rad")
    if not isinstance(limits, list) or len(limits) != 14:
        raise ValueError("profile.joint_limits_rad needs 14 [lower, upper] pairs")
    lower, upper = [], []
    for i, pair in enumerate(limits):
        low, high = vector(pair, 2, f"joint_limits_rad[{i}]")
        if not MANUAL_LOWER[i]-MANUAL_ROUNDING <= low < high <= MANUAL_UPPER[i]+MANUAL_ROUNDING:
            raise ValueError(f"joint_limits_rad[{i}] must lie inside manual range "
                             f"[{MANUAL_LOWER[i]:.4f}, {MANUAL_UPPER[i]:.4f}]")
        lower.append(low); upper.append(high)
    settings = {}
    for key, cap in (("response_timeout_s", 30.), ("arrival_tolerance_rad", .05),
                     ("arrival_speed_rad_s", .1), ("max_segment_delta_rad", .2),
                     ("min_segment_s", 30.), ("arrival_timeout_s", 120.)):
        value = p.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= cap:
            raise ValueError(f"profile.{key} must be a reviewed positive number <= {cap}")
        settings[key] = float(value)
    gripper = p.get("gripper") or {}
    if gripper.get("enabled"):
        if gripper.get("type") != "limx_2fclaw":
            raise ValueError("only the documented LimX 2-finger gripper is supported")
        for key in ("speed", "force"):
            value = gripper.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise ValueError(f"profile.gripper.{key} must be 0..100")
    return dict(host=host, port=int(port), accid=accid, names=names or None, state_order=state_order,
                lower=lower, upper=upper, gripper=gripper, profile=p,
                min_duration=max(HARDWARE_MIN_DURATION, settings["min_segment_s"]),
                max_joint_delta=min(HARDWARE_MAX_JOINT_DELTA, settings["max_segment_delta_rad"]),
                **settings)


def draft_names(feedback_names):
    """Match feedback names to course arm order; unmatched entries stay None."""
    draft = []
    for side in ("L", "R"):
        for stem in ("proximal_pitch", "proximal_roll", "proximal_yaw", "elbow",
                     "wrist_yaw", "wrist_pitch", "wrist_roll"):
            hits = [n for n in feedback_names if n == f"{stem}_{side}" or n.startswith(f"{stem}_{side}_")]
            draft.append(hits[0] if len(hits) == 1 else None)
    return draft


def vector(value, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{name} needs {size} values")
    if any(isinstance(x, bool) for x in value):
        raise ValueError(f"{name} cannot contain booleans")
    result = [float(x) for x in value]
    if not all(math.isfinite(x) for x in result):
        raise ValueError(f"{name} must be finite")
    return result


def positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def arm_target(q, lower=LOWER, upper=UPPER, names=ARM_NAMES):
    q = vector(q, 14, "MoveJ joint")
    for name, angle, low, high in zip(names, q, lower, upper):
        if not low <= angle <= high:
            raise ValueError(f"{name} outside command limits [{low}, {high}]")
    return q


def normalized_pose(pose):
    pose = vector(pose, 7, "XYZ+WXYZ pose")
    norm = math.sqrt(sum(x*x for x in pose[3:]))
    if norm < 1e-12:
        raise ValueError("quaternion cannot be zero")
    return pose[:3] + [x/norm for x in pose[3:]]


def pose_pair(left, right):
    poses = [normalized_pose(left), normalized_pose(right)]
    bounds = [((.250, .732), (-.213, .900), (-.673, .500)),
              ((.250, .732), (-.900, .213), (-.673, .500))]
    for pose, xyz_bounds in zip(poses, bounds):
        for value, (low, high) in zip(pose[:3], xyz_bounds):
            if not low <= value <= high:
                raise ValueError("MoveP target outside documented XYZ bounds")
    return poses[0] + poses[1]


def map_state(data, field="q", include_head=False, names=None):
    """Map by names; never guess that the first 14 feedback elements are arms."""
    required = list(names or ARM_NAMES) + (HEAD_NAMES if include_head else [])
    actual_names = data.get("names", [])
    values = data.get(field, [])
    if len(actual_names) != len(values) or len(set(actual_names)) != len(actual_names):
        raise ProtocolError(f"invalid names/{field} feedback")
    if any(name not in actual_names for name in required):
        raise ProtocolError("feedback joint names do not match simulation profile")
    indexed = dict(zip(actual_names, values))
    return vector([indexed[name] for name in required], len(required), field)


def flatten_pose(data):
    return normalized_pose(data["left_position"] + data["left_quat"]) + \
        normalized_pose(data["right_position"] + data["right_quat"])


def pose_errors(target, measured):
    positions, angles = [], []
    for offset in (0, 7):
        a, b = normalized_pose(target[offset:offset+7]), normalized_pose(measured[offset:offset+7])
        positions.append(math.dist(a[:3], b[:3]))
        # q and -q represent the same orientation.
        dot = abs(sum(x*y for x, y in zip(a[3:], b[3:])))
        angles.append(2*math.acos(min(1., dot)))
    return max(positions), max(angles)


class SimClient:
    def __init__(self, host="127.0.0.1", port=5000, accid="DACH_TRON2A_001",
                 response_timeout=5., joint_names=None, robot=None):
        # robot: dict from load_robot_profile; the only way past the loopback lock.
        self.robot = robot
        if robot:
            host, port, accid = robot["host"], robot["port"], robot["accid"]
            response_timeout, joint_names = robot["response_timeout_s"], robot["names"]
        elif host != "127.0.0.1":
            raise ValueError("student client accepts only 127.0.0.1 simulation; "
                             "real robot needs --robot <profile.json>")
        self.host = host
        self.port = int(port)
        self.url = f"ws://{host}:{self.port}"
        self.accid = accid
        self.timeout = positive(response_timeout, "response_timeout")
        self.joint_names = joint_names or ARM_NAMES
        if len(self.joint_names) != 14 or len(set(self.joint_names)) != 14:
            raise ValueError("profile needs 14 unique arm state names")
        self.lower, self.upper = (robot["lower"], robot["upper"]) if robot else (LOWER, UPPER)
        self.confirm = None   # hardware: callable(summary) -> raise to abort before sending
        self.seen_accids = set()
        self.ws = None
        self.events = []
        self.notifications = []
        self._condition = threading.Condition()
        self._pending = {}
        self._responses = {}
        self._async_error = None
        self._closing = threading.Event()
        self._receiver = None

    def __enter__(self):
        import websocket
        # 1.8.0 discards http_no_proxy when proxy_host is None. Supplying an
        # already-connected direct socket bypasses all environment proxy paths,
        # for loopback and for the robot LAN address alike.
        transport = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            self.ws = websocket.create_connection(self.url, timeout=self.timeout,
                                                   socket=transport, http_proxy_host=None,
                                                   http_no_proxy=[self.host, "localhost"])
        except Exception:
            transport.close()
            raise
        self.ws.settimeout(.2)
        with self._condition:
            self._async_error = None
            self._pending.clear()
            self._responses.clear()
        self._closing.clear()
        self._receiver = threading.Thread(target=self._receive_loop, daemon=True,
                                          name="tron2-sdk-receiver")
        self._receiver.start()
        return self

    def __exit__(self, *_):
        self._closing.set()
        if self.ws:
            self.ws.close()
        if self._receiver:
            self._receiver.join(timeout=1.)
        self.ws = None

    def packet(self, title, data):
        return dict(accid=self.accid, title=title, timestamp=time.time_ns()//1_000_000,
                    guid=str(uuid.uuid4()), data=data)

    def _receive_loop(self):
        """Only this thread reads frames, including fragmented WS messages."""
        import websocket
        while not self._closing.is_set():
            try:
                raw = self.ws.recv()
                if not raw:
                    if self._closing.is_set():
                        return
                    raise ProtocolError("WebSocket closed by peer")
                response = json.loads(raw)
                if not isinstance(response, dict):
                    continue
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as exc:
                if not self._closing.is_set():
                    with self._condition:
                        self._async_error = ProtocolError(f"receive failed: {exc}")
                        self._condition.notify_all()
                return
            with self._condition:
                if isinstance(response.get("accid"), str):
                    self.seen_accids.add(response["accid"])
                title = str(response.get("title", ""))
                if title.startswith("notify_"):
                    self.notifications.append(response)
                    data = response.get("data")
                    result = str(data.get("result", "")) if isinstance(data, dict) else ""
                    # The official invalid-request notification carries the original
                    # request as a string, not a dictionary.
                    if title == "notify_invalid_request" or result.startswith("fail"):
                        self._async_error = ProtocolError(f"{title}: {result or str(data)[:300]}")
                guid = response.get("guid")
                if isinstance(guid, str) and self._pending.get(guid) == title:
                    self._responses[guid] = response
                self._condition.notify_all()

    def check_async_error(self):
        with self._condition:
            if self._async_error is not None:
                raise self._async_error

    def request(self, title, data=None, expect_response=True, timeout=None):
        if self.ws is None:
            raise ProtocolError("not connected")
        message = self.packet(title, {} if data is None else data)
        expected = title.replace("request_", "response_", 1)
        sent = time.monotonic()
        budget = self.timeout if timeout is None else min(self.timeout, positive(timeout, "timeout"))
        deadline = sent+budget
        with self._condition:
            if self._async_error is not None:
                raise self._async_error
            if expect_response:
                self._pending[message["guid"]] = expected
            try:
                # Check errors and send under the same lock: after a notification is
                # dispatched, no subsequent stream sample can be sent.
                self.ws.send(json.dumps(message, allow_nan=False))
                self.events.append(dict(kind="sent", monotonic=sent, packet=message))
                if not expect_response:
                    return message
                while message["guid"] not in self._responses:
                    if self._async_error is not None:
                        raise self._async_error
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        other = sorted(self.seen_accids - {self.accid})
                        hint = f"; robot reports ACCID {other}, check profile.accid" if other else ""
                        raise TimeoutError(f"no {expected} within {budget:.3f}s{hint}")
                    self._condition.wait(remaining)
                if self._async_error is not None:
                    raise self._async_error
                response = self._responses.pop(message["guid"])
            finally:
                self._pending.pop(message["guid"], None)
                self._responses.pop(message["guid"], None)
            if response.get("accid") not in (None, self.accid):
                raise ProtocolError(f"response ACCID mismatch: sent {self.accid}, "
                                    f"robot reports {response.get('accid')}")
            payload = response.get("data")
            if not isinstance(payload, dict):
                raise ProtocolError(f"{expected}: response data must be an object")
            result = str(payload.get("result", ""))
            self.events.append(dict(kind="response", monotonic=time.monotonic(), packet=response))
            if result != "success":
                raise ProtocolError(f"{expected}: {result or 'missing result'}")
            return payload

    def with_state_order(self, data):
        """Fill empty feedback names from the profile's index layout; never overwrite real names."""
        order = STATE_ORDERS.get((self.robot or {}).get("state_order"))
        if order and not data.get("names"):
            if len(data.get("q", [])) != len(order):
                raise ProtocolError(f"state_order expects {len(order)} values, got {len(data.get('q', []))}")
            data = dict(data, names=list(order), names_from_state_order=True)
        return data

    def joint_state(self, timeout=None):
        data = self.with_state_order(self.request("request_get_joint_state", timeout=timeout))
        map_state(data, names=self.joint_names)
        map_state(data, "dq", names=self.joint_names)
        return data

    def move_pose(self, timeout=None):
        return flatten_pose(self.request("request_get_move_pose", timeout=timeout))

    def arm_target(self, q):
        return arm_target(q, self.lower, self.upper, self.joint_names)

    def _hardware_gate(self, title, duration, lines):
        """Real robot only: enforce SDK-guide timing and ask the operator before sending."""
        if not self.robot:
            return
        if duration is not None and duration < self.robot["min_duration"]:
            raise ValueError(f"real robot: duration >= {self.robot['min_duration']} s")
        if self.confirm is None:
            raise ProtocolError("real robot motion needs an operator confirmation hook")
        self.confirm("\n".join([f"[{self.accid} @ {self.url}] {title}"] + lines))

    def movej(self, q14, duration, large_move=False):
        q14 = self.arm_target(q14)
        duration = positive(duration, "duration")
        if self.robot:
            current = map_state(self.joint_state(), names=self.joint_names)
            deltas = [b-a for a, b in zip(current, q14)]
            worst = max(range(14), key=lambda i: abs(deltas[i]))
            if not large_move and abs(deltas[worst]) > self.robot["max_joint_delta"]+1e-9:
                raise ValueError(f"real robot: {self.joint_names[worst]} would move "
                                 f"{deltas[worst]:+.3f} rad > {self.robot['max_joint_delta']} rad")
            changed = [f"  {self.joint_names[i]}: {current[i]:+.4f} -> {q14[i]:+.4f} ({deltas[i]:+.4f})"
                       for i in range(14) if abs(deltas[i]) > 1e-4] or ["  (no joint changes)"]
            self._hardware_gate("MoveJ", duration, changed + [
                f"  max |delta| = {abs(deltas[worst]):.4f} rad, time = {duration} s"])
        return self.request("request_movej", dict(joint=q14, time=duration))

    def movep(self, left, right, duration):
        pos = pose_pair(left, right)
        duration = positive(duration, "duration")
        if self.robot:
            current = self.move_pose()
            moves = [math.dist(current[o:o+3], pos[o:o+3]) for o in (0, 7)]
            if max(moves) > HARDWARE_MAX_POSE_DELTA+1e-9:
                raise ValueError(f"real robot: MoveP displacement {max(moves):.4f} m > "
                                 f"{HARDWARE_MAX_POSE_DELTA} m")
            self._hardware_gate("MoveP", duration, [
                f"  {side}: {[round(x, 4) for x in current[o:o+3]]} -> {[round(x, 4) for x in pos[o:o+3]]}"
                f" ({move*1000:.1f} mm)" for side, o, move in zip(("left", "right"), (0, 7), moves)]
                + [f"  time = {duration} s"])
        return self.request("request_movep", dict(pos=pos, time=duration))

    def moveh(self, pitch_yaw, duration):
        target = vector(pitch_yaw, 2, "MoveH joint")
        if not -.78 <= target[0] <= 1.04 or not -1.57 <= target[1] <= 1.57:
            raise ValueError("head targets outside documented limits")
        duration = positive(duration, "duration")
        self._hardware_gate("MoveH (head)", duration, [f"  head -> {target}, time = {duration} s"])
        return self.request("request_moveh", dict(joint=target, time=duration))

    def servoj(self, q16):
        if self.robot:
            raise ProtocolError("ServoJ is disabled in real-robot mode of the course client")
        q16 = vector(q16, 16, "ServoJ q")
        self.arm_target(q16[:14])
        if not -.78 <= q16[14] <= 1.04 or not -1.57 <= q16[15] <= 1.57:
            raise ValueError("head targets outside documented limits")
        return self.request("request_servoj", dict(q=q16, filter_ratio=1.), False)

    def gripper(self, side, opening, speed=30., force=20.):
        if side not in ("left", "right"):
            raise ValueError("side must be left or right")
        values = vector([opening, speed, force], 3, "gripper")
        if any(not 0 <= value <= 100 for value in values):
            raise ValueError("gripper values must be in [0,100] (dimensionless)")
        if self.robot:
            if not self.robot["gripper"].get("enabled"):
                raise ProtocolError("real robot: profile.gripper.enabled is false")
            self._hardware_gate("Gripper", None, [
                f"  {side}: opening={values[0]}, speed={values[1]}, force={values[2]} (0..100)"])
        return self.request("request_set_limx_2fclaw_cmd",
                            {f"{side}_{key}": value for key, value in
                             zip(("opening", "speed", "force"), values)})

    def drag(self, command, argument=""):
        if command == "enter":
            return self.request("request_enter_drag_teach")
        allowed = {"status", "list", "start_record", "stop_record", "play", "gripper", "exit"}
        if command not in allowed:
            raise ValueError("unsupported teaching command")
        if command == "play" and not argument.strip():
            raise ValueError("play needs an existing trajectory filename")
        if command == "gripper" and argument not in ("open", "close", "toggle"):
            raise ValueError("drag gripper argument must be open/close/toggle")
        return self.request("request_drag_teach_manage", dict(cmd=command, arg=argument))

    def wait_joints(self, target, timeout=12., tolerance=.02, velocity=.05,
                    stable_samples=3, poll=.05, min_duration=0.):
        target = self.arm_target(target)
        if self.robot:  # reviewed device thresholds replace the simulation defaults
            tolerance, velocity = self.robot["arrival_tolerance_rad"], self.robot["arrival_speed_rad_s"]
            timeout = max(timeout, self.robot["arrival_timeout_s"])
        started = time.monotonic()
        deadline, stable, trace = started+positive(timeout, "timeout"), 0, []
        while time.monotonic() < deadline:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                break
            try:
                state = self.joint_state(timeout=remaining)
            except TimeoutError:
                if time.monotonic() < deadline:
                    raise
                break  # deadline hit mid-poll: report the arrival failure, not the poll
            actual = map_state(state, names=self.joint_names)
            dq = map_state(state, "dq", names=self.joint_names)
            error = max(abs(a-b) for a, b in zip(actual, target))
            speed = max(abs(x) for x in dq)
            elapsed = time.monotonic()-started
            trace.append(dict(monotonic=time.monotonic(), elapsed_s=elapsed,
                              max_error_rad=error, max_speed_rad_s=speed))
            stable = stable+1 if elapsed >= min_duration and error <= tolerance and speed <= velocity else 0
            if stable >= stable_samples:
                return dict(status="arrived", samples=len(trace), trace=trace)
            time.sleep(min(poll, max(0., deadline-time.monotonic())))
        raise TimeoutError(f"joint arrival not confirmed; last={trace[-1] if trace else None}")

    def wait_pose(self, target, timeout=12., position_tolerance=.01,
                  orientation_tolerance=.05, velocity=.05, stable_samples=3, poll=.05,
                  min_duration=0.):
        target = pose_pair(target[:7], target[7:])
        if self.robot:
            velocity = self.robot["arrival_speed_rad_s"]
            timeout = max(timeout, self.robot["arrival_timeout_s"])
        started = time.monotonic()
        deadline, stable, trace = started+positive(timeout, "timeout"), 0, []
        while time.monotonic() < deadline:
            try:
                position, angle = pose_errors(target, self.move_pose(timeout=max(deadline-time.monotonic(), 1e-3)))
                state = self.joint_state(timeout=max(deadline-time.monotonic(), 1e-3))
            except TimeoutError:
                if time.monotonic() < deadline:
                    raise
                break  # deadline hit mid-poll: report the arrival failure, not the poll
            speed = max(abs(x) for x in map_state(state, "dq", names=self.joint_names))
            elapsed = time.monotonic()-started
            trace.append(dict(monotonic=time.monotonic(), elapsed_s=elapsed, position_error_m=position,
                              orientation_error_rad=angle, max_speed_rad_s=speed))
            stable = stable+1 if elapsed >= min_duration and position <= position_tolerance and angle <= orientation_tolerance \
                and speed <= velocity else 0
            if stable >= stable_samples:
                return dict(status="arrived", samples=len(trace), trace=trace)
            time.sleep(min(poll, max(0., deadline-time.monotonic())))
        raise TimeoutError(f"pose arrival not confirmed; last={trace[-1] if trace else None}")

    def save_events(self, path):
        Path(path).write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")
