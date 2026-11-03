"""Local WebSocket mock tests; these do not validate the official controller.

Run from anywhere:  python -m unittest discover -s tests -v
(在 .venv-humble 环境下)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "python" / "robot"))

import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

try:
    from websockets.sync.server import serve
except ImportError:
    serve = None   # .venv-vision has no websockets; these tests need .venv-humble
from client import (ARM_NAMES, HEAD_NAMES, SimClient, ProtocolError, arm_target,
                    map_state, pose_pair, pose_errors)
from lessons import joint_offset, pose_offset, gripper_opening, servo_offset, prepare, prepare_head
from run import HOME, POSE


class MockController:
    def __init__(self):
        self.q = HOME.copy()+[.1, -.1]
        self.pose = POSE.copy()
        self.opening = dict(left=80., right=80.)
        self.calls = []
        self.freeze = False
        self.reject = False
        self.compliant = False
        self.notify_string = False
        self.unknown_notify = False
        self.fail_first_servo = False
        self.fragment_response = False
        self.silent_state = False
        self.silent_gripper = False
        self.server = serve(self.handle, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=3.)

    def handle(self, connection):
        for raw in connection:
            message = json.loads(raw)
            self.calls.append(message)
            title, data = message["title"], message["data"]
            if self.notify_string:
                connection.send(json.dumps(dict(title="notify_invalid_request", data=raw)))
                continue
            if self.unknown_notify:
                connection.send(json.dumps(dict(title="notify_course_info", data="informational string")))
                self.unknown_notify = False
            result = dict(result="success")
            if title == "request_get_joint_state":
                if self.silent_state:
                    continue
                # Deliberately reverse feedback to prove mapping uses names.
                result.update(names=list(reversed(ARM_NAMES+HEAD_NAMES)), q=list(reversed(self.q)),
                              dq=[0.]*16, tau=[0.]*16, timestamp=time.time_ns()//1_000_000)
            elif title == "request_get_move_pose":
                result.update(left_position=self.pose[:3], left_quat=self.pose[3:7],
                              right_position=self.pose[7:10], right_quat=self.pose[10:])
            elif title == "request_movej":
                if self.reject:
                    result["result"] = "fail_is_not_develop_mode"
                elif not self.freeze:
                    self.q[:14] = data["joint"]
            elif title == "request_movep":
                if not self.freeze:
                    self.pose = data["pos"]
            elif title == "request_moveh":
                if not self.freeze:
                    self.q[14:] = data["joint"]
            elif title == "request_servoj":
                self.q = data["q"]
                if self.fail_first_servo:
                    connection.send(json.dumps(dict(title="notify_servoJ", data=dict(result="fail_motor"))))
                continue  # Official documented ServoJ has no synchronous ACK.
            elif title == "request_set_limx_2fclaw_cmd":
                for side in ("left", "right"):
                    if f"{side}_opening" in data:
                        self.opening[side] = data[f"{side}_opening"]
            elif title == "request_get_limx_2fclaw_state":
                if self.silent_gripper:
                    continue
                result.update({f"{side}_opening": value for side, value in self.opening.items()})
            elif title == "request_drag_teach_manage":
                result.update(success=True, cmd=data["cmd"])
            elif title == "request_set_compliant_enable":
                self.compliant = data["enable"]
                result["enable"] = self.compliant
            elif title == "request_get_compliant_enable":
                result["enable"] = self.compliant
            elif title == "request_get_compliant_param":
                result.update(joint_toque=[0.]*14, cart_force=[0.]*12)
            response = dict(accid=message["accid"], guid=message["guid"],
                            title=title.replace("request_", "response_", 1), data=result)
            # An unrelated response must not satisfy a request.
            unrelated = dict(response, guid="unrelated-guid")
            connection.send(json.dumps(unrelated))
            encoded = json.dumps(response)
            connection.send([encoded[:12], encoded[12:]]) if self.fragment_response else connection.send(encoded)


@unittest.skipIf(serve is None, "websockets not installed; run under .venv-humble")
class ContractTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockController()
        self.client = SimClient(port=self.mock.port)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__()
        self.mock.close()

    def test_01_names_not_array_slice(self):
        actual = map_state(self.client.joint_state())
        self.assertEqual(actual, HOME)
        self.assertEqual(map_state(self.client.joint_state(), include_head=True)[14:], [.1, -.1])

    def test_02_movej_keeps_other_arm(self):
        result = joint_offset(self.client, delta=.06, duration=.15)
        self.assertEqual(result["target"][7:], HOME[7:])
        self.assertAlmostEqual(result["target"][3], HOME[3]+.06)
        self.assertEqual(result["arrival"]["status"], "arrived")
        self.assertGreaterEqual(result["arrival"]["samples"], 3)

    def test_03_movep_units_and_hold(self):
        result = pose_offset(self.client, delta=.01, duration=.15)
        self.assertEqual(result["target"][7:], POSE[7:])
        self.assertEqual(result["target"][3:7], POSE[3:7])
        self.assertAlmostEqual(result["target"][0], .51)
        self.assertEqual(result["arrival"]["status"], "arrived")

    def test_04_ack_is_not_arrival(self):
        self.mock.freeze = True
        target = HOME.copy()
        target[3] += .08
        self.assertEqual(self.client.movej(target, 3.)["result"], "success")
        with self.assertRaises(TimeoutError):
            self.client.wait_joints(target, timeout=.2, poll=.01)
        self.assertFalse(any(x["title"] == "request_set_limx_2fclaw_cmd" for x in self.mock.calls))

    def test_05_failed_ack_stops(self):
        self.mock.reject = True
        with self.assertRaises(ProtocolError):
            joint_offset(self.client)

    def test_06_gripper_one_side_and_no_contact_claim(self):
        result = gripper_opening(self.client, "left", 30.)
        command = next(x for x in self.mock.calls if x["title"] == "request_set_limx_2fclaw_cmd")
        self.assertEqual(set(command["data"]), {"left_opening", "left_speed", "left_force"})
        self.assertEqual(self.mock.opening["right"], 80.)
        self.assertFalse(result["grasp_contact_verified"])

    def test_07_servoj_does_not_wait_for_ack(self):
        target = HOME+[.1, -.1]
        target[3] += .03
        self.client.servoj(target)
        self.assertEqual(map_state(self.client.joint_state(), include_head=True), target)

    def test_08_drag_contract(self):
        self.assertEqual(self.client.drag("start_record")["cmd"], "start_record")
        with self.assertRaises(ValueError):
            self.client.drag("play")
        with self.assertRaises(ValueError):
            self.client.drag("gripper", "half")

    def test_09_input_contract(self):
        with self.assertRaises(ValueError):
            SimClient(host="10.192.1.2")
        with self.assertRaises(ValueError):
            self.client.movej([0.]*16, 3.)
        with self.assertRaises(ValueError):
            self.client.movej(HOME, float("nan"))
        with self.assertRaises(ValueError):
            pose_pair([.5,.28,-.25,0.,0.,0.,0.], POSE[7:])
        with self.assertRaises(ProtocolError):
            map_state(dict(names=[],q=[]))

    def test_10_quaternion_sign_equivalence(self):
        alternate = POSE.copy()
        alternate[3:7] = [-x for x in alternate[3:7]]
        self.assertEqual(pose_errors(POSE, alternate), (0., 0.))

    def test_11_invalid_request_string_is_protocol_error(self):
        self.mock.notify_string = True
        with self.assertRaises(ProtocolError) as caught:
            self.client.joint_state()
        self.assertIn("notify_invalid_request", str(caught.exception))
        self.assertIsInstance(self.client.notifications[-1]["data"], str)

    def test_12_stream_stops_after_failure_notification(self):
        self.mock.fail_first_servo = True
        started = time.monotonic()
        with self.assertRaises(ProtocolError) as caught:
            servo_offset(self.client, duration=1., rate=100.)
        elapsed = time.monotonic()-started
        count = sum(x["title"] == "request_servoj" for x in self.mock.calls)
        self.assertIn("fail_motor", str(caught.exception))
        self.assertLess(elapsed, .5)
        self.assertLess(count, 20)  # Network delivery may allow an in-flight sample.
        before = len(self.mock.calls)
        with self.assertRaises(ProtocolError):
            self.client.servoj(HOME+[0.,0.])
        time.sleep(.02)
        self.assertEqual(before, len(self.mock.calls))

    def test_13_fragmented_message_and_unknown_notification(self):
        self.mock.fragment_response = True
        self.mock.unknown_notify = True
        self.assertEqual(map_state(self.client.joint_state()), HOME)
        self.assertEqual(self.client.notifications[-1]["data"], "informational string")

    def test_14_arrival_deadline_bounds_state_request(self):
        self.mock.silent_state = True
        self.client.timeout = .4
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.client.wait_joints(HOME, timeout=.05)
        self.assertLess(time.monotonic()-started, .2)

    def test_15_gripper_deadline_bounds_state_request(self):
        self.mock.silent_gripper = True
        self.client.timeout = .4
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            gripper_opening(self.client, "left", 50., timeout=.05)
        self.assertLess(time.monotonic()-started, .2)

    def test_16_measured_limit_drift_is_rejected_without_clamping(self):
        self.mock.q[1] = -.0045
        with self.assertRaisesRegex(ValueError, "no target was clamped"):
            joint_offset(self.client)
        self.assertFalse(any(x['title'] == 'request_movej' for x in self.mock.calls))

    def test_17_explicit_prepare_uses_official_target(self):
        self.mock.q[1] = -.0045
        self.mock.q[8] = .0045
        result = prepare(self.client, duration=.15)
        self.assertEqual(result['stages'][0]['target'], HOME)
        self.assertEqual(result['initial'][1], -.0045)
        self.assertEqual(result['stages'][0]['arrival']['status'], 'arrived')

    def test_18_planned_duration_gates_arrival(self):
        started = time.monotonic()
        result = self.client.wait_pose(POSE, timeout=1., min_duration=.2, poll=.01)
        self.assertGreaterEqual(time.monotonic()-started, .2)
        self.assertGreaterEqual(result['trace'][-1]['elapsed_s'], .2)

    def test_19_explicit_head_prepare_preserves_arm_target(self):
        self.mock.q[14] = 1.04710424  # Valid model angle, beyond rounded manual bound.
        with self.assertRaisesRegex(ValueError, 'head targets'):
            self.client.servoj(self.mock.q)
        result = prepare_head(self.client, duration=.15)
        self.assertEqual(result['initial'][14], 1.04710424)
        self.assertEqual(result['final'][14:], [0.,0.])
        self.assertEqual(result['observed_arm_change_rad'], 0.)
        self.assertEqual(result['arrival']['status'], 'arrived')

    def test_20_loopback_bypasses_inherited_proxy(self):
        # No NO_PROXY fallback: an unreachable inherited proxy must be ignored.
        environment = {key:value for key,value in os.environ.items()
                       if key.lower() not in {'http_proxy','https_proxy','all_proxy','no_proxy'}}
        environment.update(HTTP_PROXY='http://127.0.0.1:1', HTTPS_PROXY='http://127.0.0.1:1',
                           http_proxy='http://127.0.0.1:1', https_proxy='http://127.0.0.1:1')
        # 1.8.0 has a loopback fallback when NO_PROXY is absent; an unrelated
        # nonempty NO_PROXY must also never override this client's explicit rule.
        for no_proxy in (None, 'example.invalid'):
            case = environment.copy()
            if no_proxy is not None:
                case.update(NO_PROXY=no_proxy, no_proxy=no_proxy)
            with self.subTest(no_proxy=no_proxy), patch.dict(os.environ, case, clear=True):
                with SimClient(port=self.mock.port, response_timeout=.4) as client:
                    self.assertEqual(map_state(client.joint_state()), HOME)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = dict(scope="local_websocket_mock_contract_only", official_controller_tested=False,
                  hardware_tested=False, tests=result.testsRun, passed=result.wasSuccessful(),
                  failures=[str(x[0]) for x in result.failures], errors=[str(x[0]) for x in result.errors])
    Path(__file__).with_name("contract_test_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
