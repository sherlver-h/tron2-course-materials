"""Four robot arms in ONE simulation, each grasping along a different preset path.

Layout: 2x2 stations (1.5 m spacing). Each station has its own table + red object
and one full copy of the teaching robot (namespaced `arm0_`..`arm3_`). All four
arms run the same stage schedule on the same physics clock:
  arm0 red    = straight line
  arm1 green  = large arc
  arm2 blue   = large polyline
  arm3 yellow = spiral
Each arm first follows its path (home -> object hover), then descends, closes,
lifts, transfers, lowers, opens, retreats -- fully synchronized for comparison.

Usage:
  python demo_four_arms.py --video          # headless: results/four_arms.mp4 + stage pngs
  python demo_four_arms.py --show           # live window
"""
import argparse
import copy
import xml.etree.ElementTree as ET
import numpy as np
import mujoco
import cv2
from build_scene import build_scene, HERE
from demo_compare_paths import (path_straight, path_arc, path_polyline, path_spiral,
                                resample_arclength)

YAW = 2.1
STATION_D = [(0., 0.), (1.5, 0.), (0., 1.5), (1.5, 1.5)]   # xy offsets per arm
PATH_STYLE = ['straight', 'arc', 'polyline', 'spiral']
PATH_RGBA = [(1., .1, .1, 1.), (.1, .9, .2, 1.), (.1, .3, 1., 1.), (1., .85, .1, 1.)]
OBJ0 = (.44, .22)     # object position in station-0 frame (far from place target)
HOME0 = (.34, .01)    # arm home (hover) in station-0 frame (far from object, for visible paths)
PLACE0 = (.28, .05)   # place target in station-0 frame; object->place distance ~0.23 m
SPIRAL_CENTER = (.37, .22)   # spiral loop offset -x (object sits near the +x workspace edge)
SPIRAL_R = .10
SPIRAL_OFFSET = (SPIRAL_CENTER[0] - OBJ0[0], SPIRAL_CENTER[1] - OBJ0[1])  # relative to object
POLY_VIAS0 = [(0.24, 0.28), (0.22, -0.02)]   # station-frame zigzag via points
Z_HOVER, Z_DESCEND, Z_LOWER = .84, .71, .725
PATH_SPEED = .10      # m/s along the approach path
PATH_N = 13           # waypoints per path (more = easier for PD to track)
PATH_MIN_DUR = .7     # s, per path segment (keeps the final tight loop trackable)

# ------------------------- scene: clone the single-arm scene 4 times --------------
def _shift_pos(el, dx, dy):
    if 'pos' in el.attrib:
        x, y, z = map(float, el.attrib['pos'].split())
        el.attrib['pos'] = f'{x+dx:.4f} {y+dy:.4f} {z}'

def build_quad_scene(extra_elems=None):
    """extra_elems: optional list of XML elements appended to the worldbody before
    compiling -- used by demo_color_table.py to inject additional furniture."""
    xml = build_scene(target=OBJ0, place=PLACE0, gripper='teaching_parallel')
    root = ET.parse(xml).getroot()
    wb = root.find('worldbody')
    base = wb.find("body[@name='base_Link']")
    furniture = [el for el in list(wb) if el.tag in ('geom', 'body') and el is not base]
    # name set of everything belonging to one arm (subtree + actuator/sensor names)
    names = {el.attrib['name'] for el in base.iter() if 'name' in el.attrib}
    for group in ('actuator', 'sensor'):
        for el in root.find(group):
            if 'name' in el.attrib:
                names.add(el.attrib['name'])
    def prefix_el(el, p):     # prefix own name and any attribute referencing arm names
        if el.attrib.get('name') in names:
            el.attrib['name'] = p + el.attrib['name']
        for k, v in el.attrib.items():
            # 'mesh' references shared read-only assets: keep them global
            if k not in ('name', 'mesh') and v in names:
                el.attrib[k] = p + v
    # IMPORTANT: clone FIRST (clones carry original names, prefix_el matches them),
    # then prefix the originals with arm0_ LAST.
    group_orig = {g: list(root.find(g)) for g in ('actuator', 'sensor', 'contact', 'equality')}
    for k in range(1, 4):
        dx, dy = STATION_D[k]; p = f'arm{k}_'
        b = copy.deepcopy(base); _shift_pos(b, dx, dy)
        for el in b.iter():
            prefix_el(el, p)
        wb.append(b)
        for f in furniture:
            c = copy.deepcopy(f); _shift_pos(c, dx, dy)
            for el in c.iter():
                if 'name' in el.attrib:
                    el.attrib['name'] = p + el.attrib['name']
            wb.append(c)
        for group, originals in group_orig.items():
            node = root.find(group)
            for el in originals:
                c = copy.deepcopy(el); prefix_el(c, p)
                node.append(c)
    # originals get the arm0_ prefix now
    for el in base.iter():
        prefix_el(el, 'arm0_')
    for group in ('actuator', 'sensor', 'contact', 'equality'):
        for el in root.find(group):
            prefix_el(el, 'arm0_')
    # quad overview camera (tilted, sees all four stations)
    ctr = (OBJ0[0] + .75, OBJ0[1] + .75)
    eye = np.array([ctr[0], -1.05, 2.45]); look = np.array([ctr[0], ctr[1], .55])
    z = (eye - look) / np.linalg.norm(eye - look)
    x = np.cross([0., 0., 1.], z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    ET.SubElement(wb, 'camera', name='quad', pos=' '.join(map(str, eye)),
                  xyaxes=' '.join(map(str, np.r_[x, y])), fovy='50')
    for el in extra_elems or []:
        wb.append(el)
    out = HERE / 'results/quad_scene.xml'
    ET.indent(root)
    ET.ElementTree(root).write(out, encoding='utf-8', xml_declaration=True)
    return mujoco.MjModel.from_xml_string(
        out.read_text(encoding='utf-8'),
        {pp.name: pp.read_bytes() for pp in (HERE / 'official/meshes').glob('*.STL')})

# ------------------------- per-arm IK / control (mirrors rig.py / dynamics.py) ----
class Arm:
    def __init__(self, model, prefix):
        self.p = prefix
        self.names = [f'{prefix}{x}_{side}_Joint' for side in 'LR'
                      for x in ['proximal_pitch', 'proximal_roll', 'proximal_yaw', 'elbow',
                                'wrist_yaw', 'wrist_pitch', 'wrist_roll']]
        self.jids = np.array([model.joint(n).id for n in self.names])
        self.qa = model.jnt_qposadr[self.jids]; self.va = model.jnt_dofadr[self.jids]
        self.aa = np.array([np.where(model.actuator_trnid[:, 0] == j)[0][0] for j in self.jids])
        self.sid = model.site(f'{prefix}tcp_L').id
        self.low, self.high = model.jnt_range[self.jids[:7]].T
        self.seed = np.array([-1.65, .4, -2.95, -1.81, -1.46, -.28, -.23])

def ik(model, work, arm, target, R, seed, max_iter=350):
    q = np.array(seed, float)
    ja = np.zeros((3, model.nv)); jr = np.zeros((3, model.nv)); er = np.zeros(3)
    ok = False
    for k in range(max_iter):
        work.qpos[arm.qa[:7]] = q; mujoco.mj_forward(model, work)
        ep = np.array(target) - work.site_xpos[arm.sid]
        mujoco.mj_jacSite(model, work, ja, jr, arm.sid)
        current = work.site_xmat[arm.sid].reshape(3, 3)
        mat = np.array(R) @ current.T; quat = np.empty(4)
        mujoco.mju_mat2Quat(quat, mat.reshape(-1))
        if quat[0] < 0: quat = -quat
        mujoco.mju_quat2Vel(er, quat, 1.)
        e = np.r_[ep, .25 * er]; J = np.vstack([ja[:, arm.va[:7]], .25 * jr[:, arm.va[:7]]])
        if np.linalg.norm(ep) < 1e-5 and np.linalg.norm(er) < 2e-4:
            ok = True; break
        lam = .005
        dq = J.T @ np.linalg.solve(J @ J.T + lam * lam * np.eye(len(e)), e)
        scale = min(1., .12 / max(np.max(np.abs(dq)), 1e-12))
        q = np.clip(q + dq * scale, arm.low + 1e-4, arm.high - 1e-4)
    return q, ok

def control(model, data, work, arm, qref, gripper):
    work.qpos[:] = data.qpos; work.qvel[:] = 0; mujoco.mj_forward(model, work)
    kp = np.array([250, 250, 180, 220, 80, 80, 60] * 2); kd = 2 * np.sqrt(kp) * .7
    tau = kp * (qref - data.qpos[arm.qa]) - kd * data.qvel[arm.va] + work.qfrc_bias[arm.va]
    data.ctrl[arm.aa] = np.clip(tau, model.actuator_ctrlrange[arm.aa, 0],
                                model.actuator_ctrlrange[arm.aa, 1])
    for aid in range(model.nu):
        if aid in arm.aa: continue
        jid = int(model.actuator_trnid[aid, 0])
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, jid) or ''
        if not name.startswith(f'{arm.p}teaching_finger'):
            continue
        qa = model.jnt_qposadr[jid]; va = model.jnt_dofadr[jid]
        ref = np.clip((.65 - gripper) / .72, 0, 1) * .04
        val = 300. * (ref - data.qpos[qa]) - 5. * data.qvel[va] + work.qfrc_bias[va]
        data.ctrl[aid] = np.clip(val, *model.actuator_ctrlrange[aid])

# ------------------------- planning / execution ------------------------------------
def plan_arm(model, work, arm, k, speed=1., min_abs=0., contact_speed=3.5,
             obj=None, home=None, place=None):
    # contact-critical stages (pads vs object) must stay slow: friction/contact
    # establishment fails beyond ~4x (see single-arm speed tests, USAGE_NOTES §2)
    cs = min(speed, contact_speed)
    dx, dy = STATION_D[k]
    home = home or (HOME0[0] + dx, HOME0[1] + dy)
    obj = obj or (OBJ0[0] + dx, OBJ0[1] + dy)
    place = place or (PLACE0[0] + dx, PLACE0[1] + dy)
    gen = {'straight': path_straight, 'arc': path_arc,
           'polyline': lambda h, o: path_polyline(h, o, vias=[(x + dx, y + dy) for x, y in POLY_VIAS0]),
           'spiral': lambda h, o: path_spiral(h, (o[0] + SPIRAL_OFFSET[0], o[1] + SPIRAL_OFFSET[1]),
                                              turns=1.5, max_r=SPIRAL_R)
           + np.array([o[0], o[1], 0.]) - np.array([o[0] + SPIRAL_OFFSET[0], o[1] + SPIRAL_OFFSET[1], 0.])}[PATH_STYLE[k]]
    path = resample_arclength(gen(home, obj), n=PATH_N)
    c, s = np.cos(YAW), np.sin(YAW); R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    seq = [('home', (*home, Z_HOVER), 1.5 / speed)]
    for i in range(len(path) - 1):
        seg = float(np.linalg.norm(path[i + 1] - path[i]))
        d = max(PATH_MIN_DUR, seg / PATH_SPEED) / speed
        seq.append((f'path{i}', path[i + 1], max(d, min_abs)))
    seq += [('hover', (*obj, Z_HOVER), 1.2 / speed),
            ('descend', (*obj, Z_DESCEND), 2. / cs), ('close', (*obj, Z_DESCEND), 1.5 / cs),
            ('lift', (*obj, Z_HOVER), 2. / cs), ('transfer', (*place, Z_HOVER), 2. / speed),
            ('lower', (*place, Z_LOWER), 2. / cs), ('open', (*place, Z_LOWER), 1. / cs),
            ('retreat', (*place, Z_HOVER), 1.5 / speed), ('rest', (*place, Z_HOVER), 1. / speed)]
    seq = [(n, p, d) for n, p, d in seq]
    rng = np.random.default_rng(7000 + k)
    for attempt in range(60):
        q = arm.seed if attempt == 0 else arm.seed + rng.uniform(-.6, .6, 7)
        rows = []
        for name, p, dur in seq:
            q, ok = ik(model, work, arm, p, R, q)
            rows.append((name, q, dur, ok))
        if all(r[3] for r in rows):
            return rows
    raise RuntimeError(f'arm{k} ({PATH_STYLE[k]}) IK planning failed')

def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--video', action='store_true')
    p.add_argument('--show', action='store_true')
    p.add_argument('--speed', type=float, default=20.,
                   help='stage time scale for non-contact stages (paths, transfer). '
                        'Tested up to 25; the demo total is dominated by contact stages')
    p.add_argument('--min-abs', type=float, default=.1,
                   help='absolute floor (s) for path segment durations after speed scaling; '
                        'keeps the tight spiral final loop trackable at high --speed')
    p.add_argument('--contact-speed', type=float, default=7.,
                   help='speed cap for contact-critical stages (descend/close/lift/lower/open). '
                        '8 passes, 10 fails (pads cannot push through in 0.2s)')
    p.add_argument('--stagger', type=float, default=.4,
                   help='delay between arms for contact-critical stages (s); must exceed the '
                        'descend stage duration (~0.3s at default contact speed) to keep '
                        'pad-object contact islands separated')
    p.add_argument('--camera', default='quad')
    p.add_argument('--render-every', type=int, default=8, help='sim steps between video frames')
    p.add_argument('--fps', type=int, default=40, help='video fps (40 + every-8 = 3x slow motion)')
    return p

def main():
    a = build_parser().parse_args()
    run(build_quad_scene(), a)

def run(model, a, out_name='four_arms', objs=None, verify_bodies=None):
    """objs: optional [(x,y)x4] grasp targets (default: per-station OBJ0).
    verify_bodies: optional body names checked against the place targets at the end
    (default: the built-in red per-station objects)."""
    data = mujoco.MjData(model)
    work = mujoco.MjData(model)
    arms = [Arm(model, f'arm{k}_') for k in range(4)]
    plans = []
    for k, arm in enumerate(arms):
        dx, dy = STATION_D[k]
        obj = (objs[k] if objs else None)
        rows = plan_arm(model, work, arm, k, speed=a.speed, min_abs=a.min_abs,
                        contact_speed=a.contact_speed, obj=obj)
        plans.append(rows)
        tgt = obj or (OBJ0[0] + dx, OBJ0[1] + dy)
        print(f'arm{k} [{PATH_STYLE[k]:9s}] target=({tgt[0]:.3f},{tgt[1]:.3f}) planned: {len(rows)} waypoints, '
              f'total {sum(r[2] for r in rows):.1f}s', flush=True)
    # initial state: all arms at home pose, teaching fingers open
    for arm, rows in zip(arms, plans):
        data.qpos[arm.qa[:7]] = rows[0][1]
    for aid in range(model.nu):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT,
                                 int(model.actuator_trnid[aid, 0])) or ''
        if 'teaching_finger' in name:
            data.qpos[model.jnt_qposadr[int(model.actuator_trnid[aid, 0])]] = 0.
    mujoco.mj_forward(model, data)
    qprev = [data.qpos[arm.qa].copy() for arm in arms]   # 14-dof (L arm planned, R held)
    grip_open, grip_close = .65, -.07
    n_stages = max(len(rows) for rows in plans)
    viewer = None
    if a.show:
        from mujoco import viewer as mjviewer
        viewer = mjviewer.launch_passive(model, data)
    # write video incrementally: 4 robots @720p -> ~2.7 MB/frame, never store in RAM
    writer = None
    if a.video:
        import imageio_ffmpeg
        writer = imageio_ffmpeg.write_frames(
            str(HERE / f'results/{out_name}.mp4'), (960, 720), fps=a.fps, codec='libx264',
            quality=8, pix_fmt_in='rgb24', pix_fmt_out='yuv420p', macro_block_size=16)
        writer.send(None)
    renderer = mujoco.Renderer(model, height=720, width=960) if a.video else None
    out_dir = HERE / f'results/{out_name}'; out_dir.mkdir(parents=True, exist_ok=True)
    dt = model.opt.timestep
    # contact-critical stages are slightly staggered per arm: with all four pad-object
    # contact islands active at once, the joint constraint solve can stick pads on the
    # object top (observed: tcp stalls 35mm high). Solo descends always push through.
    STAGGER = {'descend': .0, 'close': .15, 'lower': .0, 'open': .15}
    ARM_DELAY = [0., a.stagger, 2 * a.stagger, 3 * a.stagger]
    for si in range(n_stages):
        name = plans[0][si][0]
        dur = max(plans[k][si][2] for k in range(4))
        extra = ARM_DELAY[3] if name in STAGGER else 0.
        steps = max(1, round((dur + extra) / dt))
        for step in range(steps):
            t = (step + 1) * dt
            for k, arm in enumerate(arms):
                rows = plans[k]
                if si >= len(rows): continue
                delay = ARM_DELAY[k] if rows[si][0] in STAGGER else 0.
                if t <= delay:      # hold previous pose during this arm's stagger delay
                    control(model, data, work, arm, qprev[k], grip_open if si < len(rows) - 8 else grip_close)
                    continue
                u = min((t - delay) / rows[si][2], 1.); sm = 10 * u**3 - 15 * u**4 + 6 * u**5
                qend = qprev[k].copy(); qend[:7] = rows[si][1]
                qref = qprev[k] + (qend - qprev[k]) * sm
                st = rows[si][0]
                grip = grip_open
                if st == 'close':   grip = grip_open + (grip_close - grip_open) * sm
                elif st in ('lift', 'transfer', 'lower'): grip = grip_close
                elif st == 'open':  grip = grip_close + (grip_open - grip_close) * sm
                control(model, data, work, arm, qref, grip)
            mujoco.mj_step(model, data)
            if viewer is not None:
                viewer.sync()
            if renderer is not None and step % a.render_every == 0:
                renderer.update_scene(data, camera=a.camera)
                fr = renderer.render().copy()
                anchors = {0: (30, 690), 1: (650, 690), 2: (30, 60), 3: (650, 60)}
                for k in range(4):
                    col = tuple(int(255 * v) for v in PATH_RGBA[k][:3])
                    cv2.putText(fr, f'arm{k} {PATH_STYLE[k]} | {name}', anchors[k],
                                cv2.FONT_HERSHEY_SIMPLEX, .7, col, 2)
                writer.send(np.ascontiguousarray(fr))
        for k in range(4):
            if si < len(plans[k]):
                qprev[k][:7] = plans[k][si][1]
        if renderer is not None:
            renderer.update_scene(data, camera=a.camera)
            cv2.imwrite(str(out_dir / f'{si:02d}_{name}.png'),
                        cv2.cvtColor(renderer.render().copy(), cv2.COLOR_RGB2BGR))
    print(f'stage {si:2d} {name:9s} done  t={data.time:.1f}s', flush=True)
    # per-arm verification after the final stage: object on its place marker?
    if si == n_stages - 1:
        for k in range(4):
            dx, dy = STATION_D[k]
            vname = verify_bodies[k] if verify_bodies else ('object' if k == 0 else f'arm{k}_object')
            bid = model.body(vname).id
            p = data.xpos[bid]
            err = float(np.linalg.norm(p[:2] - np.array([PLACE0[0] + dx, PLACE0[1] + dy])))
            print(f'  arm{k} [{PATH_STYLE[k]:9s}] final object xyz={np.round(p, 3).tolist()} '
                  f'place_err={err*1000:.1f}mm -> {"PASS" if err < .025 else "NOT VERIFIED"}', flush=True)
    if renderer is not None:
        renderer.close()
        writer.close()
        print(f'saved results/{out_name}.mp4')
    if viewer is not None:
        viewer.close()

if __name__ == '__main__':
    main()
