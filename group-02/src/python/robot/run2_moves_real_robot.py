"""Pick an action category, get suggestions of doable moves, then play them.

suggest  -- list action categories, and (with --type) every move the robot can
           actually do in that category, each with beat count, duration,
           worst per-beat joint jump, and hardware requirements (gripper/head).
play     -- build the beat sequence for --move/--type/--seq and run it:
           offline packet preview by default; --robot <profile.json> --send
           moves a real robot (operator confirms once, then the beat loop runs
           uninterrupted). ServoJ/drag/compliant stay disabled on real robots.

All named poses were hand-checked against client.LOWER/UPPER and the head
limits used by client.moveh. Symmetric poses mirror the left arm with
[q0,-q1,-q2,q3,-q4,q5,q6]; wave/salute/point_side_* are asymmetric on purpose.
Hardware limit to remember: shoulder roll L>=0, R<=0, so the arms can never
cross in front of the chest -- a real "clap" is impossible; see chest_beat.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

from client import (SimClient, arm_target, positive, load_robot_profile,
                    draft_names, map_state)
from lessons import HOME

# ---------------------------------------------------------------------------
# Named poses. `pair` mirrors the left arm onto the right; `left_only` keeps
# the right arm in `ready`. Every pose is validated by arm_target below.
MIRROR = lambda q: [q[0], -q[1], -q[2], q[3], -q[4], q[5], q[6]]
READY7 = [0.0, 0.30, 0.0, -1.30, 0.30, 0.0, 0.0]
pair = lambda q: q + MIRROR(q)
left_only = lambda q: q + MIRROR(READY7)
right_only = lambda q: READY7 + q  # right arm acts, left arm stays in ready

NAMED = {
    "rest": [0.] * 14,                                   # arms hanging (power-on pose)
    "ready": pair(READY7),
    "home": list(HOME),
    "overhead": pair([2.1, 0.35, 0.0, -0.4, 0.0, 0.3, 0.0]),      # arms up high
    "side": pair([0.1, 1.5, 0.0, -0.15, 0.0, 0.0, 0.0]),          # straight out to the sides
    "point_fwd": pair([1.15, 0.25, 0.0, -0.35, 0.0, 0.0, 0.0]),   # point straight ahead
    "point_side_l": left_only([0.2, 1.3, 0.0, -0.3, 0.0, 0.0, 0.0]),
    "point_side_r": right_only([0.2, -1.3, 0.0, -0.3, 0.0, 0.0, 0.0]),
    "guard": pair([0.9, 0.45, -0.35, -1.55, 0.7, 0.0, 0.2]),      # boxing guard
    "jab": pair([1.5, 0.35, -0.1, -0.45, 0.3, 0.0, 0.0]),         # straight punch
    "hook": pair([0.6, 0.9, 0.0, -1.7, 0.9, 0.0, 0.0]),           # bent-arm hook
    "chest": pair([0.9, 0.7, -0.6, -1.8, 0.6, 0.3, 0.0]),         # fists at chest
    "salute": left_only([1.2, 0.6, -1.0, -2.2, 1.2, 0.3, 0.0]),   # left hand to brow
    "wave_up": left_only([1.6, 0.7, -0.5, -1.9, 1.3, 0.4, 0.0]),  # hand up, wrist bent up
    "wave_down": left_only([1.6, 0.7, -0.5, -1.9, 1.3, -0.4, 0.0]),
    "wave2_up": pair([1.6, 0.7, -0.5, -1.9, 1.3, 0.4, 0.0]),      # both arms waving
    "wave2_down": pair([1.6, 0.7, -0.5, -1.9, 1.3, -0.4, 0.0]),
    "bow_fwd": pair([1.9, 0.4, 0.0, -0.9, 0.4, 0.0, 0.0]),        # arms reach forward-down
    "swing_out": pair([-0.6, 0.3, 0.0, -2.3, 0.8, 0.0, 0.0]),
    "robot_pose": pair([0.2, 0.8, 0.0, -0.6, 0.0, 0.5, 0.5]),
    "freeze_up": pair([1.5, 1.2, 0.0, -0.1, 0.0, 0.0, 0.0]),
    "cross_chest": pair([0.7, 0.15, 1.0, -1.9, 0.0, 0.3, 0.0]),
}
for _name, _q in NAMED.items():
    arm_target(_q)  # fail fast if any named pose ever leaves the limits

# ---------------------------------------------------------------------------
# Move library: name -> (category, one-line description, beat list).
# A beat is a pose name, ("grip", opening) or ("head", [pitch, yaw]). Moves
# start and end at "ready" so they chain cleanly.
MOVES = {
    # 问候
    "wave": ("greeting", "左手举高挥动（腕部摆动3次）",
             ["ready", "wave_up", "wave_down", "wave_up", "wave_down", "wave_up", "wave_down", "ready"]),
    "double_wave": ("greeting", "双手举高一起挥动",
                    ["ready", "wave2_up", "wave2_down", "wave2_up", "wave2_down",
                     "wave2_up", "wave2_down", "ready"]),
    "salute_nod": ("greeting", "左手敬礼保持，放下后点头两次",
                   ["ready", "salute", "salute", "ready",
                    ("head", [0.5, 0.0]), ("head", [0.0, 0.0]), "ready"]),
    "bow": ("greeting", "双臂前伸低头致意，再收回",
            ["ready", "bow_fwd", ("head", [0.6, 0.0]), "bow_fwd", ("head", [0.0, 0.0]), "ready"]),
    # 指向
    "point_fwd": ("pointing", "双臂指向前方并停住",
                  ["ready", "point_fwd", "point_fwd", "ready"]),
    "point_side": ("pointing", "左臂指向身体左侧并停住",
                   ["ready", "point_side_l", "point_side_l", "ready"]),
    "point_scan": ("pointing", "左指→收→右指，左右扫描",
                   ["ready", "point_side_l", "ready", "point_side_r", "ready"]),
    # 拳击
    "jab_combo": ("boxing", "抱架→直拳→抱架，左右对称出拳",
                  ["ready", "guard", "jab", "guard", "jab", "guard", "ready"]),
    "hook_combo": ("boxing", "抱架→勾拳→抱架，左右对称",
                   ["ready", "guard", "hook", "guard", "hook", "guard", "ready"]),
    # 健身操
    "open_close": ("exercise", "模拟开合跳：垂臂→侧平举→上举→收回",
                   ["rest", "side", "overhead", "side", "ready", "rest"]),
    "arm_circle": ("exercise", "双臂大回环（4点近似一圈）",
                   ["ready", "overhead", "side", "swing_out", "home", "ready"]),
    "stretch": ("exercise", "抱胸→侧平举→上举的拉伸序列",
                ["ready", "cross_chest", "side", "overhead", "ready"]),
    # 节拍（肩roll左右异号、双臂不能胸前交叉，真拍手做不到 → 用握拳+夹爪开合代替）
    "chest_beat": ("beats", "双拳收胸+夹爪开合两次（拍手的替代动作）",
                   ["ready", "chest", ("grip", 15.), ("grip", 90.), "chest", ("grip", 15.), ("grip", 90.), "ready"]),
    "head_bop": ("beats", "头部左右甩动打节拍",
                 ["ready", ("head", [0.0, 0.8]), ("head", [0.0, -0.8]), ("head", [0.0, 0.0]), "ready"]),
    "nod": ("beats", "点头两次",
            ["ready", ("head", [0.5, 0.0]), ("head", [0.0, 0.0]), "ready"]),
    # 舞步（沿用 run2_dance_real_robot 的编排片段）
    "swing": ("dance", "两次大幅度甩臂",
              ["ready", "swing_out", "ready", "swing_out", "ready"]),
    "robot_step": ("dance", "机械舞：定格→机器人姿态→定格",
                   ["ready", "robot_pose", "freeze_up", "robot_pose", "ready"]),
    "freeze": ("dance", "高举双臂定格",
               ["ready", "freeze_up", "freeze_up", "ready"]),
}

CATEGORY_TITLES = {
    "greeting": "问候 greeting",
    "pointing": "指向 pointing",
    "boxing": "拳击 boxing",
    "exercise": "健身操 exercise",
    "beats": "节拍 beats（真拍手受肩roll限位限制，见 chest_beat 说明）",
    "dance": "舞步 dance",
}


def move_stats(move):
    """(beats, n_movej, n_grip, n_head, worst_per_beat_delta_rad) for a move."""
    beats, n_movej, n_grip, n_head = [], 0, 0, 0
    for beat in MOVES[move][2]:
        if isinstance(beat, tuple):
            n_grip += beat[0] == "grip"
            n_head += beat[0] == "head"
        else:
            n_movej += 1
            beats.append(NAMED[beat])
    worst = max((max(abs(a - b) for a, b in zip(q0, q1))
                 for q0, q1 in zip(beats, beats[1:])), default=0.)
    return len(MOVES[move][2]), n_movej, n_grip, n_head, worst


def selection(args):
    """Resolve --type/--move/--seq into an ordered move list (no duplicates)."""
    if args.move:
        return [args.move]
    if args.seq:
        names = [name.strip() for name in args.seq.split(",") if name.strip()]
        unknown = [n for n in names if n not in MOVES]
        if unknown:
            raise ValueError(f"unknown move(s): {unknown}; see `suggest`")
        return names
    if args.type:
        return [m for m, (cat, _, _) in MOVES.items() if cat == args.type or args.type == "all"]
    raise ValueError("play needs --move <name>, --type <category> or --seq a,b,c; see `suggest`")


def print_suggestions(args, robot):
    """List categories, or every doable move in one category with real numbers.

    Durations are shown at the *effective* beat length: on a real robot every
    beat (MoveJ/MoveH/gripper hold) must last at least robot['min_duration'],
    so the advisory times use max(--duration, that floor). Per-beat joint
    jumps are compared against the profile's max_segment_delta_rad.
    """
    dur = args.duration
    cap = None
    if robot:
        dur = max(dur, robot["min_duration"])
        cap = robot["max_joint_delta"]
        cap_note = (f"真机约束: 单段 <= {cap} rad, 每拍 >= {robot['min_duration']} s "
                    f"(建议 --duration {dur:.0f}), "
                    f"夹爪 {'可用' if robot['gripper'].get('enabled') else '未启用'}")
    else:
        cap_note = "未加载真机profile: 以下为课程限位下的通用建议"
    print(f"== 动作建议 ({cap_note}) ==", flush=True)
    cats = list(CATEGORY_TITLES) if args.type in (None, "all") else [args.type]
    for cat in cats:
        print(f"\n[{CATEGORY_TITLES[cat]}]", flush=True)
        for move, (mcat, desc, _) in MOVES.items():
            if mcat != cat:
                continue
            beats, n_movej, n_grip, n_head, worst = move_stats(move)
            flags = []
            if cap is not None and worst > cap:
                flags.append(f"超单段{cap} rad(编排整段确认)")
            elif worst > .2:
                flags.append("大角度")
            elif worst <= .9:
                flags.append("入门友好")
            if n_grip:
                flags.append("需夹爪" if not robot or robot["gripper"].get("enabled") else "需夹爪(未启用!)")
            if n_head:
                flags.append("含头部")
            print(f"  {move:<14} {desc}", flush=True)
            print(f"  {'':<14} {beats}拍/{n_movej} MoveJ, {n_grip} 夹爪, {n_head} 头部; "
                  f"约 {beats * dur:.0f}s(生效每拍{dur:.0f}s); "
                  f"最大单拍关节跳变 {worst:.2f} rad; {' / '.join(flags)}", flush=True)
    easy = [m for m in MOVES if move_stats(m)[4] <= .9 and not move_stats(m)[2]]
    hard = [m for m in MOVES if move_stats(m)[4] > 1.2]
    print(f"\n建议: 第一次上真机先试 [{', '.join(easy)}], "
          f"真机记得 --duration >= {dur:.0f} s; "
          f"大角度动作 [{', '.join(hard)}] 靠编排一次确认, 勿逐拍增量发送。", flush=True)
    print("播放: python3 run2_moves_real_robot.py play --move wave  "
          "(或 --type greeting / --seq wave,nod)", flush=True)


def move_packets(moves, duration, speed, force):
    """Flatten the selected moves into (title, data) beat packets."""
    packets = []
    for move in moves:
        for beat in MOVES[move][2]:
            if isinstance(beat, tuple):
                kind, value = beat
                if kind == "grip":
                    data = {f"{side}_{key}": v for side in ("left", "right")
                            for key, v in (("opening", value), ("speed", speed), ("force", force))}
                    packets.append(("request_set_limx_2fclaw_cmd", data))
                else:
                    packets.append(("request_moveh", dict(joint=value, time=duration)))
            else:
                packets.append(("request_movej", dict(joint=NAMED[beat], time=duration)))
    for title, data in packets:
        if title == "request_movej":
            arm_target(data["joint"])  # raises if any beat leaves the limits
    return packets


def perform_packets(client, packets, duration, args):
    """The run2_dance_real_robot beat loop, shared by every selected move."""
    confirm = getattr(client, "confirm", None)
    if confirm:
        moves_summary = ", ".join(selection(args))
        confirm(f"play: {len(packets)} beats / {len(packets) * duration:.0f} s, "
                f"moves={moves_summary}, speed={args.speed} force={args.force}; "
                f"pose-to-pose moves beyond the per-segment cap are covered by this one confirmation")
    client.confirm = (lambda summary: print(summary, flush=True)) if client.robot else None
    counts = {"movej": 0, "gripper": 0, "head": 0, "gripper_skipped": 0}
    gripper_ok = None
    for title, data in packets:
        if title == "request_movej":
            # Named poses can be ~2 rad apart, over the reviewed per-segment
            # cap; large_move is allowed because the operator confirmed the
            # whole selection above (same pattern as run2_dance_real_robot).
            client.movej(data["joint"], data["time"], large_move=True)
            # Without waiting, the next MoveJ overwrites this target while the
            # arm is still moving and the beat is effectively skipped.
            client.wait_joints(data["joint"], timeout=data["time"] + 8.,
                               min_duration=data["time"] * .9)
            counts["movej"] += 1
        else:
            if "claw" in title and client.robot:
                # The profile may say gripper.enabled=false; probe once and
                # skip the gripper beats instead of aborting mid-routine.
                if gripper_ok is None:
                    try:
                        client.request("request_get_limx_2fclaw_state")
                        gripper_ok = True
                    except Exception:
                        gripper_ok = False
                        print("gripper state unavailable; skipping gripper beats", flush=True)
                if not gripper_ok:
                    time.sleep(data.get("time", duration))  # hold the beat
                    counts["gripper_skipped"] += 1
                    continue
            client.request(title, data)
            time.sleep(data.get("time", duration))  # hold the beat
            counts["gripper" if "claw" in title else "head"] += 1
    if confirm:
        client.confirm = confirm
    return counts


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["suggest", "play"])
    p.add_argument("--type", choices=list(CATEGORY_TITLES) + ["all"],
                   help="suggest: 只列出该类型; play: 顺序播放该类型全部动作")
    p.add_argument("--move", choices=sorted(MOVES), help="play: 只播放这一个动作")
    p.add_argument("--seq", help="play: 逗号分隔的动作序列, 如 wave,nod,freeze")
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
    p.add_argument("--output", type=Path, default=Path("sdk_result_moves.json"))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    args.duration = positive(args.duration, "duration")
    if not 1. <= args.duration <= 30.:
        raise ValueError("teaching example duration 1..30 s")
    robot = None
    if args.robot:
        robot = load_robot_profile(args.robot, require_names=True)
        if args.action == "play" and args.duration < robot["min_duration"]:
            raise ValueError(f"real robot: --duration >= {robot['min_duration']} s (SDK guide)")
    if any(not 0. <= value <= 100. for value in (args.speed, args.force)):
        raise ValueError("gripper speed/force must be 0..100 (dimensionless)")
    profile = json.loads(args.profile.read_text(encoding="utf-8")) if args.profile else {}
    client = SimClient(args.host, args.port, args.accid, joint_names=profile.get("arm_state_names"),
                       robot=robot)
    client.confirm = None  # play asks itself via perform_packets -> operator_confirm below
    live = "hardware_live" if robot else "loopback_live"
    result = dict(action=args.action, mode=live if args.send else "offline_preview",
                  endpoint=client.url, accid=client.accid, hardware_verified=False)
    if robot:
        result["robot_profile"] = str(args.robot.resolve())
    try:
        if args.action == "suggest":
            print_suggestions(args, robot)
            result["status"] = "preview_only"
            result["note"] = "suggest is advisory only; nothing was sent or previewed as packets"
        elif not args.send:
            moves = selection(args)
            packets = move_packets(moves, args.duration, args.speed, args.force)
            result["result"] = dict(moves=moves, beats=len(packets),
                                    total_time_s=len(packets) * args.duration)
            result["packets"] = [client.packet(title, data) for title, data in packets]
            result["status"] = "preview_only"
            result["note"] = "Illustrative fixture; no connection, no robot motion, no measured state."
        else:
            def operator_confirm(summary):
                print(summary, flush=True)
                try:
                    answer = input("Real robot will move. Clear the area, hand on E-stop. Type y to send: ")
                except EOFError:
                    answer = ""
                if answer.strip().lower() != "y":
                    raise RuntimeError("operator declined; nothing sent")
            moves = selection(args)
            packets = move_packets(moves, args.duration, args.speed, args.force)
            client.confirm = operator_confirm
            with client:
                counts = perform_packets(client, packets, args.duration, args)
            result["result"] = dict(moves=moves, beats=len(packets), **counts,
                                    total_time_s=len(packets) * args.duration)
            result["status"] = "passed"
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    result["notifications"] = client.notifications
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.send and client.events:
        client.save_events(args.output.with_suffix(".events.json"))
    print(json.dumps(dict(status=result["status"], mode=result["mode"], output=str(args.output.resolve()),
                          error=result.get("error")), ensure_ascii=False))
    return int(result["status"] == "failed")


if __name__ == "__main__":
    sys.exit(main())
