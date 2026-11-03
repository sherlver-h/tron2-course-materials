"""Run without --send for an offline packet preview; --send uses loopback only.

Real robot: --robot <profile.json> --send. Every motion prints its target and
waits for the operator to type y. ServoJ and state-changing drag/compliant
commands stay disabled in real-robot mode.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from client import (SimClient, arm_target, positive, pose_pair, vector, load_robot_profile,
                    draft_names, map_state, HARDWARE_MAX_POSE_DELTA)
from lessons import joint_offset, pose_offset, pose_offset_corrected, servo_offset, gripper_opening, prepare, prepare_head, HOME, ROUTES

POSE = [.5, .28, -.25, 1., 0., 0., 0., .5, -.28, -.25, 1., 0., 0., 0.]


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["query", "prepare", "prepare-head", "demo", "movej", "movep", "servoj", "gripper", "drag", "compliant"])
    p.add_argument("--send", action="store_true", help="connect to the local simulator")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--accid", default="DACH_TRON2A_001")
    p.add_argument("--profile", type=Path, help="optional JSON with exactly 14 arm_state_names")
    p.add_argument("--robot", type=Path, help="real-robot profile JSON (url, accid, names, limits); "
                   "replaces --host/--port/--accid/--profile")
    p.add_argument("--side", choices=["left", "right"], default="left")
    p.add_argument("--joint", type=int, choices=range(7), default=3, help="zero-based single-arm joint index")
    p.add_argument("--axis", type=int, choices=range(3), default=0, help="0=x,1=y,2=z")
    p.add_argument("--delta", type=float, help="rad for MoveJ/ServoJ; m for MoveP")
    p.add_argument("--duration", type=float, default=3.)
    p.add_argument("--correct", type=int, choices=range(4), default=0,
                   help="movep: outer-loop corrections after the first MoveP (each asks y on a real robot)")
    p.add_argument("--prepare-route", choices=sorted(ROUTES), default="direct",
                   help="explicit DACH preparation: official waypoint 03, 01,02,03, or zero (arms hanging)")
    p.add_argument("--with-gripper", action="store_true", help="demo also moves the selected gripper")
    p.add_argument("--rate", type=float, default=300.)
    p.add_argument("--opening", type=float, default=50.)
    p.add_argument("--speed", type=float, default=30.)
    p.add_argument("--force", type=float, default=20.)
    p.add_argument("--cmd", default="status", help="drag: enter/status/list/start_record/stop_record/play/gripper/exit")
    p.add_argument("--arg", default="")
    p.add_argument("--enable", choices=["true", "false"], help="compliant switch; omission queries only")
    p.add_argument("--output", type=Path, default=Path("sdk_result.json"))
    return p


def preview(client, args):
    """Schematic packet construction; HOME/POSE are not live state or safe targets."""
    if args.action == "query":
        return [client.packet(title, {}) for title in ("request_get_joint_state", "request_get_move_pose")]
    if args.action == "prepare-head":
        return [client.packet("request_moveh", dict(joint=[0.,0.], time=args.duration))]
    if args.action in ("prepare", "demo"):
        points = ROUTES[args.prepare_route]
        return [client.packet("request_movej", dict(joint=left+[q*s for q,s in zip(left,[1,-1,-1,1,-1,1,-1])],
                                                      time=args.duration)) for left in points]
    if args.action in ("movej", "servoj"):
        q = HOME.copy()
        q[args.joint + (0 if args.side == "left" else 7)] += args.delta
        arm_target(q)
        title, data = ("request_movej", dict(joint=q, time=args.duration)) if args.action == "movej" else \
            ("request_servoj", dict(q=q+[0., 0.], filter_ratio=1.))
    elif args.action == "movep":
        pose = POSE.copy()
        pose[args.axis + (0 if args.side == "left" else 7)] += args.delta
        title, data = "request_movep", dict(pos=pose_pair(pose[:7], pose[7:]), time=args.duration)
    elif args.action == "gripper":
        title = "request_set_limx_2fclaw_cmd"
        data = {f"{args.side}_{key}": value for key, value in
                zip(("opening", "speed", "force"), (args.opening, args.speed, args.force))}
    elif args.action == "drag":
        title, data = ("request_enter_drag_teach", {}) if args.cmd == "enter" else \
            ("request_drag_teach_manage", dict(cmd=args.cmd, arg=args.arg))
    else:
        title, data = ("request_get_compliant_enable", {}) if args.enable is None else \
            ("request_set_compliant_enable", dict(enable=args.enable == "true"))
    return [client.packet(title, data)]


def operator_confirm(summary):
    print(summary, flush=True)
    try:
        answer = input("Real robot will move. Clear the area, hand on E-stop. Type y to send: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "y":
        raise RuntimeError("operator declined; nothing sent")


def hardware_query(client, args):
    """Raw state first, so a profile without verified names can still be completed."""
    state = client.request("request_get_joint_state")
    result = dict(joint_state=state, move_pose_xyz_wxyz=client.move_pose(),
                  feedback_names=state.get("names", []), seen_accids=sorted(client.seen_accids))
    try:
        result["arm_q_by_profile"] = dict(zip(client.joint_names,
                                              map_state(client.with_state_order(state), names=client.joint_names)))
    except Exception as exc:
        result["name_mapping_error"] = str(exc)
    names = draft_names(result["feedback_names"])
    draft = dict(client.robot["profile"], arm_state_names=names)
    draft_path = args.output.with_suffix(".profile_draft.json")
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    result["profile_draft"] = str(draft_path)
    result["profile_draft_note"] = "Review every name (left 7 then right 7); None = no unique match."
    return result


def execute(client, args):
    if args.action == "query":
        if client.robot:
            return hardware_query(client, args)
        return dict(joint_state=client.joint_state(), move_pose_xyz_wxyz=client.move_pose())
    if args.action == "prepare-head":
        return prepare_head(client, args.duration)
    if args.action in ("prepare", "demo"):
        result = dict(preparation=prepare(client, args.duration, args.prepare_route))
        if args.action == "demo":
            result["movej"] = joint_offset(client, args.side, args.joint, .06, args.duration)
            result["movep"] = pose_offset(client, args.side, args.axis, .01, args.duration)
            if args.with_gripper:
                result["gripper"] = gripper_opening(client, args.side, args.opening, args.speed, args.force)
        return result
    if args.action == "movej":
        return joint_offset(client, args.side, args.joint, args.delta, args.duration)
    if args.action == "movep" and args.correct:
        return pose_offset_corrected(client, args.side, args.axis, args.delta, args.duration, args.correct)
    if args.action == "movep":
        return pose_offset(client, args.side, args.axis, args.delta, args.duration)
    if args.action == "servoj":
        return servo_offset(client, args.side, args.joint, args.delta, args.duration, args.rate)
    if args.action == "gripper":
        return gripper_opening(client, args.side, args.opening, args.speed, args.force)
    if args.action == "drag":
        return dict(response=client.drag(args.cmd, args.arg), trajectory_execution_verified=False)
    if args.enable is not None:
        client.request("request_set_compliant_enable", dict(enable=args.enable == "true"))
    return dict(enable=client.request("request_get_compliant_enable"),
                params=client.request("request_get_compliant_param"), compliance_behavior_verified=False)


def main(argv=None):
    args = parser().parse_args(argv)
    args.duration = positive(args.duration, "duration")
    args.rate = positive(args.rate, "rate")
    if not 1. <= args.duration <= 30. or args.rate > 300.:
        raise ValueError("teaching example duration 1..30 s, rate <=300 Hz")
    args.delta = (.01 if args.action == "movep" else .06) if args.delta is None else args.delta
    if not abs(args.delta) <= (.02 if args.action == "movep" else .2):
        raise ValueError("teaching offset capped at 0.02 m / 0.2 rad")
    robot = None
    if args.robot:
        robot = load_robot_profile(args.robot, require_names=args.action != "query")
        if args.action == "servoj":
            raise ValueError("real robot: ServoJ disabled in the course client")
        if args.action == "drag" and args.cmd not in ("status", "list"):
            raise ValueError("real robot: only drag status/list here; use the official teach flow")
        if args.action == "compliant" and args.enable is not None:
            raise ValueError("real robot: compliant switching is not exposed here")
        if args.action in ("prepare", "prepare-head", "demo", "movej", "movep") and \
                args.duration < robot["min_duration"]:
            raise ValueError(f"real robot: --duration >= {robot['min_duration']} s (SDK guide)")
        if abs(args.delta) > (HARDWARE_MAX_POSE_DELTA if args.action == "movep" else robot["max_joint_delta"]):
            raise ValueError(f"real robot offset capped at {HARDWARE_MAX_POSE_DELTA} m / "
                             f"{robot['max_joint_delta']} rad")
    if args.action == "gripper":
        values = vector([args.opening, args.speed, args.force], 3, "gripper")
        if any(not 0. <= value <= 100. for value in values):
            raise ValueError("gripper values must be 0..100 (dimensionless)")
    if args.action == "drag":
        allowed = {"enter", "status", "list", "start_record", "stop_record", "play", "gripper", "exit"}
        if args.cmd not in allowed:
            raise ValueError("unsupported drag teaching command")
        if args.cmd == "play" and not args.arg.strip():
            raise ValueError("play requires an existing trajectory name")
        if args.cmd == "gripper" and args.arg not in ("open", "close", "toggle"):
            raise ValueError("drag gripper argument must be open/close/toggle")
    profile = json.loads(args.profile.read_text(encoding="utf-8")) if args.profile else {}
    client = SimClient(args.host, args.port, args.accid, joint_names=profile.get("arm_state_names"),
                       robot=robot)
    client.confirm = operator_confirm if robot else None
    live = "hardware_live" if robot else "loopback_live"
    result = dict(action=args.action, mode=live if args.send else "offline_preview",
                  endpoint=client.url, accid=client.accid, hardware_verified=False)
    if robot:
        result["robot_profile"] = str(args.robot.resolve())
    try:
        if args.send:
            with client:
                result["result"] = execute(client, args)
        else:
            result["packets"] = preview(client, args)
            result["note"] = "Illustrative fixture; no connection, no robot motion, no measured state."
        result["status"] = "passed" if args.send else "preview_only"
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    result["notifications"] = client.notifications
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.send:
        client.save_events(args.output.with_suffix(".events.json"))
    print(json.dumps(dict(status=result["status"], mode=result["mode"], output=str(args.output.resolve()),
                          error=result.get("error")), ensure_ascii=False))
    return int(result["status"] == "failed")


if __name__ == "__main__":
    sys.exit(main())
