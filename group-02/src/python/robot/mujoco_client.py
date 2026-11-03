"""MuJoCo-backed client, duck-type compatible with client.SimClient.

Lets the dance/show scripts execute their choreography on the official TRON2
MuJoCo model with visualization, without any network or real robot:

    python run2_dance_real_robot.py dance --sim                      # 实时窗口
    python run2_moves_show_real_robot.py show --sim --sim-video out.mp4   # 无头录像

Requires mujoco (and imageio-ffmpeg for --sim-video): run under .venv-vision.
Joints are driven kinematically (mj_forward, smoothstep interpolation), which
is sufficient for choreography visualization; there is no dynamics/contacts.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import mujoco

HERE = Path(__file__).resolve().parent
OFFICIAL_XML = HERE.parent / "vision" / "official" / "xml" / "robot_grasper.xml"
SCENE_DIR = HERE.parent / "vision" / "scenes"

ARM = ["proximal_pitch", "proximal_roll", "proximal_yaw", "elbow",
       "wrist_yaw", "wrist_pitch", "wrist_roll"]
ARM14 = [f"{n}_L_Joint" for n in ARM] + [f"{n}_R_Joint" for n in ARM]
GRIPPER_L = ["grasper_base_L_Joint", "grasper_L_jaw_left_Joint", "grasper_L_jaw_right_Joint"]
GRIPPER_R = ["grasper_base_R_Joint", "grasper_R_jaw_left_Joint", "grasper_R_jaw_right_Joint"]
HEAD2 = ["head_yaw_Joint", "head_pitch_Joint"]
# grasper_base range 0..1.8326 rad, jaw slide range 0..0.0375 m (from the model XML)
GRIPPER_BASE_MAX = 1.8326
JAW_MAX = 0.0375


def build_scene_xml() -> Path:
    """Official model, welded base, mocap helpers removed, floor + dance camera."""
    root = ET.parse(OFFICIAL_XML).getroot()
    root.attrib["model"] = "TRON2 dance sim"
    root.find("compiler").attrib["meshdir"] = str((OFFICIAL_XML.parent / ".." / "meshes").resolve())
    wb = root.find("worldbody")
    vis = root.find("visual")
    if vis is None:
        vis = ET.SubElement(root, "visual")
    ET.SubElement(vis, "global", offwidth="1280", offheight="720")
    base = wb.find("body[@name='base_Link']")
    for j in list(base.findall("joint")):
        base.remove(j)                       # weld the base: no floating joint
    for b in list(wb.findall("body")):
        if b.attrib.get("mocap") == "true":
            wb.remove(b)                     # drop target visualization mocap bodies
    # The official worldbody already ships a checker ground plane and two
    # directional lights; only add a camera-side fill light and the view camera.
    ET.SubElement(wb, "light", name="dance_fill", directional="true",
                  pos="3 -3 4", dir="-3 3 -4", diffuse=".9 .9 .9",
                  specular=".2 .2 .2", castshadow="false")
    eye = np.array([1.7, -1.7, 1.35])
    look = np.array([0., 0., 0.75])
    z = (eye - look) / np.linalg.norm(eye - look)
    x = np.cross([0., 0., 1.], z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    ET.SubElement(wb, "camera", name="dance", pos=" ".join(map(str, eye)),
                  xyaxes=" ".join(map(str, np.r_[x, y])), fovy="45")
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    out = SCENE_DIR / "dance_scene.xml"
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
    return out


class MujocoClient:
    """Drop-in replacement for SimClient in the dance/show execution paths.

    robot is None (same as loopback mode), so the scripts skip operator
    confirmations and gripper probing; every motion is simulated.
    """

    def __init__(self, *, video: Path | None = None, fps: float = 30.,
                 dt: float = 0.005, viewer: bool = True):
        if not OFFICIAL_XML.is_file():
            raise FileNotFoundError(f"missing {OFFICIAL_XML}; keep src/python/vision/official/")
        self.robot = None
        self.confirm = None
        self.notifications = []
        self.events = []
        self.url = "mujoco-sim://local"
        self.accid = "DACH_TRON2A_SIM"
        self.dt = float(dt)
        self.t = 0.0
        self.fps = float(fps)
        self.model = mujoco.MjModel.from_xml_path(str(build_scene_xml()))
        self.data = mujoco.MjData(self.model)
        names = ARM14 + GRIPPER_L + GRIPPER_R + HEAD2
        self.qadr = {n: self.model.joint(n).qposadr for n in names}
        self.cur = {n: 0.0 for n in names}      # XML zero pose
        self._arm_from = self._arm_to = None
        self._arm_dur = 0.0
        self._vh = None
        self._renderer = None
        self._writer = None
        self._video_path = None
        self._next_frame_t = 0.0
        if video is None and viewer:
            from mujoco import viewer as mjviewer
            self._vh = mjviewer.launch_passive(self.model, self.data)
            self._render()                       # show the zero pose immediately
        elif video is not None:
            self._video_path = Path(video)
            self._video_path.parent.mkdir(parents=True, exist_ok=True)
            self._renderer = mujoco.Renderer(self.model, height=720, width=1280)

    # ----------------------------------------------------------- rendering
    def _render(self):
        if self._vh is not None:
            if not self._vh.is_running():
                raise RuntimeError("viewer closed by user")
            self._vh.sync()
            time.sleep(0.001)
        elif self._renderer is not None and self.t >= self._next_frame_t - 1e-9:
            if self._writer is None:
                import imageio_ffmpeg
                self._writer = imageio_ffmpeg.write_frames(
                    str(self._video_path), (1280, 720), fps=self.fps, quality=8)
                self._writer.send(None)
            self._renderer.update_scene(self.data, camera="dance")
            self._writer.send(self._renderer.render().tobytes())
            self._next_frame_t += 1.0 / self.fps

    def _forward(self):
        mujoco.mj_forward(self.model, self.data)
        self.t += self.dt
        self._render()

    def _set(self, names, values):
        for n, v in zip(names, values):
            self.data.qpos[self.qadr[n]] = float(v)
            self.cur[n] = float(v)

    def _animate(self, names, to, seconds):
        """Smoothstep interpolation; steps physics kinematically and renders."""
        seconds = max(float(seconds), self.dt)
        start = np.array([self.cur[n] for n in names])
        target = np.asarray(to, dtype=float)
        steps = max(1, int(round(seconds / self.dt)))
        for i in range(1, steps + 1):
            s = i / steps
            s = s * s * (3 - 2 * s)
            self._set(names, start + (target - start) * s)
            self._forward()

    # ------------------------------------------------- SimClient interface
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._vh is not None:
            self._vh.close()
            self._vh = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None

    def packet(self, title, data):
        return dict(accid=self.accid, title=title, timestamp=time.time_ns() // 1_000_000,
                    data=data)

    def movej(self, q14, duration, large_move=False):
        self.events.append(dict(kind="sim_movej", t=self.t, duration=duration))
        self._arm_from = np.array([self.cur[n] for n in ARM14])
        self._arm_to = np.asarray(q14, dtype=float)
        if self._arm_to.shape != (14,):
            raise ValueError(f"MoveJ expects 14 joints, got {self._arm_to.shape}")
        self._arm_dur = float(duration)

    def wait_joints(self, target=None, timeout=12., tolerance=.02, velocity=.05,
                    min_duration=0.0):
        """Run the pending movej interpolation to completion (the 'arrival')."""
        if self._arm_to is None:
            return
        dur = max(self._arm_dur, float(min_duration or 0.))
        start, to = self._arm_from, self._arm_to
        steps = max(1, int(round(dur / self.dt)))
        for i in range(1, steps + 1):
            s = i / steps
            s = s * s * (3 - 2 * s)
            self._set(ARM14, start + (to - start) * s)
            self._forward()
        self._arm_to = None

    def request(self, title, data=None, expect_response=True, timeout=None):
        data = data or {}
        if title == "request_moveh":
            self.events.append(dict(kind="sim_moveh", t=self.t, data=data))
            self._animate(HEAD2, data["joint"], data.get("time", 1.0))
            return dict(ok=True)
        if title == "request_set_limx_2fclaw_cmd":
            self.events.append(dict(kind="sim_gripper", t=self.t, data=data))
            for side, names in (("left", GRIPPER_L), ("right", GRIPPER_R)):
                opening = float(data.get(f"{side}_opening", 50.))
                ratio = min(max(opening, 0.), 100.) / 100.0
                self._animate(names, [ratio * GRIPPER_BASE_MAX, ratio * JAW_MAX, ratio * JAW_MAX],
                              0.6)
            return dict(ok=True)
        if title == "request_get_limx_2fclaw_state":
            left = self.cur["grasper_base_L_Joint"] / GRIPPER_BASE_MAX * 100.0
            right = self.cur["grasper_base_R_Joint"] / GRIPPER_BASE_MAX * 100.0
            return dict(left_opening=left, right_opening=right)
        raise ValueError(f"sim client does not implement {title}")

    def joint_state(self, timeout=None):
        return dict(names=list(ARM14) + HEAD2,
                    q=[self.cur[n] for n in ARM14] + [self.cur[n] for n in HEAD2])

    def save_events(self, path):
        Path(path).write_text(json.dumps(self.events, ensure_ascii=False, indent=2),
                              encoding="utf-8")
