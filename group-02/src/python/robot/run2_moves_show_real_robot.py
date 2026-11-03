"""A fixed showcase routine built from the run2_moves_real_robot library.

show -- plays the curated ~3-minute program (63 beats at the default
        --duration 3.0 s): nod -> wave -> point_scan -> jab_combo ->
        open_close -> chest_beat -> head_bop -> swing -> robot_step ->
        freeze -> bow. Offline: packet preview only; real robot:
        --robot <profile.json> --send (operator confirms once, then the
        beat loop runs uninterrupted, same as run2_dance_real_robot).
list -- print the program with per-move beat counts and joint statistics.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from client import SimClient, positive, load_robot_profile
from run2_moves_real_robot import MOVES, move_packets, perform_packets, move_stats

# The showcase program: 63 beats = 189 s at --duration 3.0, same pacing as the
# 60-beat dance demo. Order matters: slow openers, peak at chest_beat/swing,
# robot_step + freeze as the finale before the bow.
SHOW = ["nod", "wave", "point_scan", "jab_combo", "open_close",
        "chest_beat", "head_bop", "swing", "robot_step", "freeze", "bow"]


def print_program(duration, robot=None):
    """Program sheet; on a real robot times use the per-beat duration floor
    (every beat -- MoveJ/MoveH/gripper hold -- must last at least
    robot['min_duration']) and per-beat jumps are flagged against the
    profile's max_segment_delta_rad."""
    cap = robot["max_joint_delta"] if robot else None
    if robot:
        duration = max(duration, robot["min_duration"])
        print(f"== run2_moves_show 节目单 (真机: 每拍 >= {duration:.0f} s, "
              f"单段 <= {cap} rad, 大角度拍由整段确认覆盖) ==", flush=True)
    else:
        print("== run2_moves_show 节目单 (离线建议, 未加载真机profile) ==", flush=True)
    total = 0
    for i, move in enumerate(SHOW, 1):
        beats, n_movej, n_grip, n_head, worst = move_stats(move)
        total += beats
        flag = f"  [超单段{cap} rad]" if cap is not None and worst > cap else ""
        print(f"  {i:>2}. {move:<13} {MOVES[move][1]}{flag}", flush=True)
        print(f"      {beats}拍/{n_movej} MoveJ, {n_grip} 夹爪, {n_head} 头部; "
              f"约 {beats * duration:.0f}s; 最大单拍跳变 {worst:.2f} rad", flush=True)
    print(f"  合计 {total} 拍, 约 {total * duration:.0f}s @ 每拍 {duration:.0f}s", flush=True)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["show", "list"])
    p.add_argument("--send", action="store_true", help="connect to the local simulator")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--accid", default="DACH_TRON2A_001")
    p.add_argument("--profile", type=Path, help="optional JSON with exactly 14 arm_state_names")
    p.add_argument("--robot", type=Path, help="real-robot profile JSON (url, accid, names, limits); "
                   "replaces --host/--port/--accid/--profile")
    p.add_argument("--duration", type=float, default=3.)
    p.add_argument("--speed", type=float, default=30.)
    p.add_argument("--force", type=float, default=20.)
    p.add_argument("--output", type=Path, default=Path("sdk_result_show.json"))
    p.add_argument("--sim", action="store_true", help="MuJoCo 仿真执行（可视化），不连接网络/真机；需要 mujoco，请在 .venv-vision 下运行")
    p.add_argument("--sim-video", type=Path, help="仿真无头模式：输出 mp4 路径（不设则打开实时窗口）")
    p.add_argument("--sim-fps", type=float, default=30., help="仿真录像帧率")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    args.duration = positive(args.duration, "duration")
    if not 1. <= args.duration <= 30.:
        raise ValueError("teaching example duration 1..30 s")
    if args.sim and (args.send or args.robot):
        raise ValueError("--sim 与 --send/--robot 互斥（仿真模式不连接任何外部目标）")
    robot = None
    if args.robot:
        robot = load_robot_profile(args.robot, require_names=True)
        if args.action == "show" and args.duration < robot["min_duration"]:
            raise ValueError(f"real robot: --duration >= {robot['min_duration']} s (SDK guide)")
    if any(not 0. <= value <= 100. for value in (args.speed, args.force)):
        raise ValueError("gripper speed/force must be 0..100 (dimensionless)")
    profile = json.loads(args.profile.read_text(encoding="utf-8")) if args.profile else {}
    if args.sim:
        try:
            from mujoco_client import MujocoClient
        except ImportError as exc:
            raise SystemExit("--sim 需要 mujoco：请在 .venv-vision 环境下运行") from exc
        client = MujocoClient(video=args.sim_video, fps=args.sim_fps)
    else:
        client = SimClient(args.host, args.port, args.accid, joint_names=profile.get("arm_state_names"),
                           robot=robot)
    live = "hardware_live" if robot else ("mujoco_sim" if args.sim else "loopback_live")
    result = dict(action=args.action, mode=live if (args.send or args.sim) else "offline_preview",
                  endpoint=client.url, accid=client.accid, hardware_verified=False,
                  show=SHOW)
    if robot:
        result["robot_profile"] = str(args.robot.resolve())
    try:
        if args.action == "list":
            print_program(args.duration, robot)
            result["status"] = "preview_only"
            result["note"] = "list is advisory only; nothing was sent or previewed as packets"
        elif not args.send and not args.sim:
            packets = move_packets(SHOW, args.duration, args.speed, args.force)
            result["result"] = dict(moves=SHOW, beats=len(packets),
                                    total_time_s=len(packets) * args.duration)
            result["packets"] = [client.packet(title, data) for title, data in packets]
            result["status"] = "preview_only"
            result["note"] = "Illustrative fixture; no connection, no robot motion, no measured state."
        else:
            packets = move_packets(SHOW, args.duration, args.speed, args.force)
            if args.sim:
                client.confirm = None
            else:
                def operator_confirm(summary):
                    print(summary, flush=True)
                    try:
                        answer = input("Real robot will move. Clear the area, hand on E-stop. Type y to send: ")
                    except EOFError:
                        answer = ""
                    if answer.strip().lower() != "y":
                        raise RuntimeError("operator declined; nothing sent")
                client.confirm = operator_confirm
            with client:
                args.move, args.type, args.seq = None, None, ",".join(SHOW)
                counts = perform_packets(client, packets, args.duration, args)
            result["result"] = dict(moves=SHOW, beats=len(packets), **counts,
                                    total_time_s=len(packets) * args.duration)
            result["status"] = "passed"
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    result["notifications"] = client.notifications
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if (args.send or args.sim) and client.events:
        client.save_events(args.output.with_suffix(".events.json"))
    print(json.dumps(dict(status=result["status"], mode=result["mode"], output=str(args.output.resolve()),
                          error=result.get("error")), ensure_ascii=False))
    return int(result["status"] == "failed")


if __name__ == "__main__":
    sys.exit(main())
