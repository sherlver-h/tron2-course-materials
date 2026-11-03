"""Side-by-side comparison of 4 preset TCP approach paths, played SIMULTANEOUSLY.

Paths all go from the same home (hover) to the same object hover point at z=0.84:
  red    straight line
  green  large arc (quadratic Bezier detour)
  blue   large polyline (zigzag through 2 far via points)
  yellow spiral (1.5-turn Archimedean approach into the object)

The scene is drawn with capsule line segments; 4 mocap markers animate along the
4 paths at the same time. Optionally validates each path with position-only IK
(--check-ik) and records an mp4 (--video).

Usage:
  python demo_compare_paths.py --video          # headless: saves results/compare_paths.mp4
  python demo_compare_paths.py --show           # live MuJoCo window
  python demo_compare_paths.py --x 0.35 --y 0.10 --home-x 0.45 --home-y 0.20 --video
"""
import argparse
from pathlib import Path
import numpy as np
import mujoco
import cv2
import xml.etree.ElementTree as ET
from build_scene import build_scene, HERE

Z = .84
PATHS = [  # name, color rgba
    ('straight', (1., .1, .1, 1.)),
    ('arc',      (.1, .9, .2, 1.)),
    ('polyline', (.1, .3, 1., 1.)),
    ('spiral',   (1., .85, .1, 1.)),
]

# ---------------- path generators (all: array of xyz, first=home, last=object) ----
def path_straight(h, o, n=120):
    t = np.linspace(0, 1, n)
    return np.column_stack([h[0] + t * (o[0] - h[0]), h[1] + t * (o[1] - h[1]), np.full(n, Z)])

def path_arc(h, o, n=160, bulge=.28):
    h, o = np.asarray(h), np.asarray(o)
    mid = (h + o) / 2
    d = o - h
    perp = np.array([-d[1], d[0]]) / np.linalg.norm(d)
    ctrl = mid + perp * bulge                      # control point off to one side
    t = np.linspace(0, 1, n)
    xy = (1 - t)[:, None] ** 2 * h + 2 * ((1 - t) * t)[:, None] * ctrl + t[:, None] ** 2 * o
    return np.column_stack([xy, np.full(n, Z)])

def path_polyline(h, o, n=200, vias=None):
    vias = vias or [(0.24, 0.28), (0.22, -0.02)]    # big zigzag detour
    pts = [np.asarray(h, float)] + [np.array(v, float) for v in vias] + [np.asarray(o, float)]
    seg = np.array([np.linalg.norm(pts[i + 1] - pts[i]) for i in range(len(pts) - 1)])
    cum = np.concatenate([[0], np.cumsum(seg)]) / seg.sum()
    t = np.linspace(0, 1, n)
    xy = np.empty((n, 2))
    for k in range(len(pts) - 1):
        m = (t >= cum[k]) & (t <= cum[k + 1] + 1e-9)
        u = np.clip((t[m] - cum[k]) / (cum[k + 1] - cum[k]), 0, 1)
        xy[m] = pts[k] + u[:, None] * (pts[k + 1] - pts[k])
    return np.column_stack([xy, np.full(n, Z)])

def path_spiral(h, o, n=240, turns=2.0, max_r=None):
    h, o = np.asarray(h, float), np.asarray(o)
    r0 = np.linalg.norm(h - o)
    if max_r is not None:
        r0 = min(r0, max_r)   # keep the whole loop inside the reachable workspace
    th0 = np.arctan2(h[1] - o[1], h[0] - o[0])
    t = np.linspace(0, 1, n)
    th = th0 + 2 * np.pi * turns * (1 - t)
    r = r0 * (1 - t) ** 1.2
    xy = o + np.column_stack([r * np.cos(th), r * np.sin(th)])
    xy[-1] = o
    return np.column_stack([xy, np.full(n, Z)])

GENERATORS = {'straight': path_straight, 'arc': path_arc, 'polyline': path_polyline, 'spiral': path_spiral}

def resample_arclength(pts, n=240):
    """Resample a polyline at equal arc-length spacing (constant marker speed)."""
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    d /= d[-1]
    out = np.empty((n, 3))
    for j in range(3):
        out[:, j] = np.interp(np.linspace(0, 1, n), d, pts[:, j])
    return out

# ---------------- scene construction ------------------------------------------------
def build_comparison_scene(obj, home):
    xml = build_scene(target=obj, place=(.32, .10), gripper='teaching_parallel')
    root = ET.parse(xml).getroot()
    wb = root.find('worldbody')
    paths = {name: resample_arclength(GENERATORS[name](home, obj)) for name, _ in PATHS}
    for (name, rgba), pts in zip(PATHS, paths.values()):
        for a, b in zip(pts[::2], pts[1::2]):      # capsule segments (every 2nd point)
            ET.SubElement(wb, 'geom', type='capsule',
                          fromto=' '.join(f'{v:.4f}' for v in np.r_[a, b]),
                          size='.004', rgba=' '.join(map(str, rgba)),
                          contype='0', conaffinity='0')
    for k, (_, rgba) in enumerate(PATHS):          # animated mocap markers
        ET.SubElement(wb, 'body', name=f'mark{k}', mocap='true',
                      pos=' '.join(map(str, paths[PATHS[k][0]][0])),
                      )
        b = wb.find(f"body[@name='mark{k}']")
        ET.SubElement(b, 'geom', type='sphere', size='.009', rgba=' '.join(map(str, rgba)),
                      contype='0', conaffinity='0')
    out = HERE / 'results/compare_scene.xml'
    ET.indent(root)
    ET.ElementTree(root).write(out, encoding='utf-8', xml_declaration=True)
    model = mujoco.MjModel.from_xml_string(
        out.read_text(encoding='utf-8'),
        {p.name: p.read_bytes() for p in (HERE / 'official/meshes').glob('*.STL')})
    return model, paths

def check_ik(model_paths, obj):
    """Position-only IK check along each path (chained seed, feasibility hint)."""
    from rig import Rig
    r = Rig(target=tuple(obj), gripper='teaching_parallel')
    print('position-IK check (chained seed, max_iter=400):')
    for name, pts in model_paths.items():
        q = np.array([-1.65, .4, -2.95, -1.81, -1.46, -.28, -.23])  # dynamics.plan seed
        errs = []
        for p in pts[:: max(1, len(pts) // 12)]:
            _, tr, ok = r.ik(p, seed=q, max_iter=400)
            errs.append(tr[-1]['position_error_m'])
        print(f'  {name:9s} worst position error = {max(errs):.1e} m '
              f'({"reachable" if max(errs) < 1e-3 else "OFF-reach at z=0.84 (mocap demo only)"})')

# ---------------- playback ----------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--x', type=float, default=.35); p.add_argument('--y', type=float, default=.10)
    p.add_argument('--home-x', type=float, default=.55); p.add_argument('--home-y', type=float, default=.25)
    p.add_argument('--camera', default='detail', choices=['overview', 'detail', 'top'],
                   help='camera used for the live window / video')
    p.add_argument('--duration', type=float, default=8., help='seconds for one full playback')
    p.add_argument('--loops', type=int, default=1)
    p.add_argument('--check-ik', action='store_true')
    p.add_argument('--video', action='store_true')
    p.add_argument('--show', action='store_true')
    a = p.parse_args()
    obj, home = (a.x, a.y), (a.home_x, a.home_y)
    print('legend:  red=straight  green=arc  blue=polyline  yellow=spiral')
    model, paths = build_comparison_scene(obj, home)
    if a.check_ik:
        check_ik(paths, obj)
    data = mujoco.MjData(model)
    names = [n for n, _ in PATHS]
    viewer = None
    if a.show:
        from mujoco import viewer as mjviewer
        viewer = mjviewer.launch_passive(model, data)
    renderer = mujoco.Renderer(model, height=720, width=960) if a.video else None
    frames = []
    dt = 1. / 240.
    try:
        for loop in range(a.loops):
            t0 = loop * a.duration
            steps = int(a.duration / dt)
            for k in range(steps):
                t = (k + 1) * dt
                frac = min(t / a.duration, 1.)
                for j, name in enumerate(names):
                    pts = paths[name]
                    idx = min(int(frac * (len(pts) - 1)), len(pts) - 1)
                    data.mocap_pos[j] = pts[idx]
                mujoco.mj_forward(model, data)
                if viewer is not None:
                    viewer.sync()
                if renderer is not None and k % 4 == 0:
                    renderer.update_scene(data, camera=a.camera)
                    frame = renderer.render().copy()
                    cv2.putText(frame, f'{frac*100:5.1f}%', (25, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
                    for j, (name, rgba) in enumerate(PATHS):
                        c = tuple(int(255 * v) for v in rgba[:3])
                        cv2.putText(frame, name, (25, 75 + 30 * j),
                                    cv2.FONT_HERSHEY_SIMPLEX, .8, c, 2)
                    frames.append(frame)
                if viewer is None and renderer is None:
                    break
    finally:
        if renderer is not None:
            renderer.close()
        if viewer is not None:
            viewer.close()
    if frames:
        import imageio_ffmpeg
        out = HERE / 'results/compare_paths.mp4'
        writer = imageio_ffmpeg.write_frames(str(out), (960, 720), fps=25, codec='libx264',
                                             quality=8, pix_fmt_in='rgb24',
                                             pix_fmt_out='yuv420p', macro_block_size=16)
        writer.send(None)
        for f in frames:
            writer.send(np.ascontiguousarray(f))
        writer.close()
        print('saved', out)

if __name__ == '__main__':
    main()
