"""MujocoClient kinematic tests; skipped when mujoco is unavailable (.venv-humble).

Run from anywhere:  python -m unittest discover -s tests -v   (under .venv-vision)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "python" / "robot"))

try:
    import mujoco  # noqa: F401
    from mujoco_client import MujocoClient, ARM14, GRIPPER_BASE_MAX
except ImportError:
    mujoco = None


@unittest.skipIf(mujoco is None, "mujoco not installed; run under .venv-vision")
class MujocoClientTests(unittest.TestCase):
    def setUp(self):
        self.client = MujocoClient(viewer=False)   # headless, no window, no video

    def tearDown(self):
        self.client.close()

    def test_movej_arrives_at_target(self):
        q = [0.3, 0.5, -0.2, -1.0, 0.1, 0.0, 0.2]
        target = q + [q[0], -q[1], -q[2], q[3], -q[4], q[5], q[6]]
        self.client.movej(target, 0.2)
        self.client.wait_joints(min_duration=0.2)
        for name, value in zip(ARM14, target):
            self.assertAlmostEqual(self.client.cur[name], value, places=5)

    def test_head_and_gripper_requests(self):
        self.client.request("request_moveh", dict(joint=[0.5, -0.3], time=0.2))
        self.assertAlmostEqual(self.client.cur["head_yaw_Joint"], 0.5, places=5)
        self.assertAlmostEqual(self.client.cur["head_pitch_Joint"], -0.3, places=5)
        self.client.request("request_set_limx_2fclaw_cmd",
                            dict(left_opening=50., left_speed=30, left_force=20,
                                 right_opening=0., right_speed=30, right_force=20))
        self.assertAlmostEqual(self.client.cur["grasper_base_L_Joint"],
                               0.5 * GRIPPER_BASE_MAX, places=5)
        self.assertAlmostEqual(self.client.cur["grasper_base_R_Joint"], 0.0, places=5)

    def test_rejects_unsupported_request(self):
        with self.assertRaises(ValueError):
            self.client.request("request_servoj", dict(q=[0.] * 16))


if __name__ == '__main__':
    unittest.main()
