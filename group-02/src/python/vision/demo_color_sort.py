"""Color-based grasping: each arm finds ITS colored block by vision, then grasps it.

Physical constraint: in the 2x2 quad layout every arm can only reach its own station
table (the center gap is outside all workspaces). So color sorting is done PER STATION:
on each station table sits the usual RED distractor block plus ONE colored target
block (arm0=blue, arm1=green, arm2=blue, arm3=green by default). A top-down camera
per station feeds an HSV color mask; the largest blob inside the expected spawn zone
is back-projected to a world xy, and the arm plans its (preset-style) path to the
MEASURED position -- a real color-localization pipeline, not fixed coordinates.

Usage:
  python demo_color_sort.py --video                 # default colors: blue green blue green
  python demo_color_sort.py --colors blue green red green --seed 3 --video
  python demo_color_sort.py --show                  # live window
"""
import argparse
import xml.etree.ElementTree as ET
import numpy as np
import mujoco
import cv2
import demo_four_arms as fa

TABLE_Z = .68
BLOCK_Z = TABLE_Z + .020
W, H = 1280, 960
FOVY = 48.
Z_PLANE = TABLE_Z + .04          # block top face plane for back-projection

# HSV thresholds (OpenCV: H 0..179)
HSV = {'blue':  ((95, 80, 60), (130, 255, 255)),
       'green': ((40, 80, 60), (85, 255, 255)),
       'red':   ((0, 100, 60), (10, 255, 255))}
RGBA = {'blue': '0 .25 1 1', 'green': '0 .85 .12 1', 'red': '.9 .015 .01 1'}
# spawn zone for the colored block, per station (validated reachable rect minus margin)
SPAWN = (0.32, 0.42, -0.02, 0.18)   # x0,x1,y0,y1 in station frame
MIN_DIST = 0.10                      # from the red distractor (OBJ0) and the place marker


def color_sort_elems(colors, seed):
    """Per station: one top camera + one colored target block (random position)."""
    rng = np.random.default_rng(seed)
    els = []
    for k in range(4):
        dx, dy = fa.STATION_D[k]
        cam = ET.Element('camera', name=f'sortcam{k}',
                         pos=f'{.48 + dx} {dy} 1.85', xyaxes='1 0 0 0 1 0', fovy=str(FOVY))
        els.append(cam)
        color = colors[k]
        # random spawn position inside the safe zone, away from distractor/place
        x0, x1, y0, y1 = SPAWN
        while True:
            x = float(rng.uniform(x0, x1)) + dx
            y = float(rng.uniform(y0, y1)) + dy
            d_red = np.hypot(x - (fa.OBJ0[0] + dx), y - (fa.OBJ0[1] + dy))
            d_pl = np.hypot(x - (fa.PLACE0[0] + dx), y - (fa.PLACE0[1] + dy))
            if d_red > MIN_DIST and d_pl > MIN_DIST:
                break
        b = ET.Element('body', name=f'sort{k}_block', pos=f'{x} {y} {BLOCK_Z}',
                       quat='.4975710479 0 0 .8674232256')
        ET.SubElement(b, 'freejoint', name=f'sort{k}_block_free')
        ET.SubElement(b, 'geom', name=f'sort{k}_block_geom', type='box', size='.018 .018 .020',
                      mass='.05', rgba=RGBA[color], friction='1.2 .02 .001',
                      condim='4', solref='.004 1', margin='0')
        els.append(b)
        print(f'  station{k}: {color} block spawned at ({x - dx:.3f}, {y - dy:.3f})', flush=True)
    return els


def detect(model, data, k, color):
    """Render the station camera, HSV-segment the color, back-project to world xy.
    Also saves visualization (rgb + centroid overlay / mask) to results/color_sort/."""
    from pathlib import Path
    cid = model.camera(f'sortcam{k}').id
    with mujoco.Renderer(model, height=H, width=W) as r:
        r.update_scene(data, camera=f'sortcam{k}')
        img = r.render()
    lo, hi = HSV[color]
    mask = cv2.inRange(cv2.cvtColor(img, cv2.COLOR_RGB2HSV), np.array(lo), np.array(hi))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        return None
    order = np.argsort(stats[1:, cv2.CC_STAT_AREA])[::-1] + 1   # largest first
    fx = (H / 2) / np.tan(np.radians(FOVY) / 2)
    cx, cy = model.cam_pos[cid][:2]
    # accept the largest blob whose back-projected position is inside the spawn zone
    dx, dy = fa.STATION_D[k]
    out_dir = Path(fa.HERE) / 'results/color_sort'
    out_dir.mkdir(parents=True, exist_ok=True)
    overlay = img.copy()
    result = None
    for i in order:
        if stats[i, cv2.CC_STAT_AREA] < 500:
            break
        u, v = cents[i]
        t = model.cam_pos[cid][2] - Z_PLANE
        x = cx + (u - W / 2) / fx * t
        y = cy - (v - H / 2) / fx * t
        in_zone = (SPAWN[0] + dx - .03 <= x <= SPAWN[1] + dx + .03 and
                   SPAWN[2] + dy - .03 <= y <= SPAWN[3] + dy + .03)
        mark = (0, 255, 0) if in_zone else (255, 255, 0)
        cv2.drawMarker(overlay, (int(u), int(v)), mark, cv2.MARKER_CROSS, 40, 3)
        cv2.putText(overlay, f'{color} ({x:.3f},{y:.3f}){"" if in_zone else " rejected"}',
                    (int(u) + 25, int(v)), cv2.FONT_HERSHEY_SIMPLEX, .8, mark, 2)
        if in_zone and result is None:
            result = (x, y)
    cv2.imwrite(str(out_dir / f'vision{k}_{color}_rgb.png'),
                cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(out_dir / f'vision{k}_{color}_mask.png'), mask)
    return result


def main():
    p = fa.build_parser()
    p.description = __doc__
    p.add_argument('--colors', nargs=4, choices=['blue', 'green', 'red'],
                   default=['blue', 'green', 'blue', 'green'],
                   help='target color per arm (station table gets one block of this color)')
    p.add_argument('--seed', type=int, default=1, help='block spawn randomization seed')
    a = p.parse_args()
    print('building color-sort scene, target colors:', a.colors)
    model = fa.build_quad_scene(extra_elems=color_sort_elems(a.colors, a.seed))
    # settle the blocks on a scratch MjData, then run vision on the settled poses
    tmp = mujoco.MjData(model)
    for _ in range(1500):
        mujoco.mj_step(model, tmp)
    objs = []
    for k in range(4):
        xy = detect(model, tmp, k, a.colors[k])
        if xy is None:
            raise RuntimeError(f'color detection failed on station {k} ({a.colors[k]})')
        dx, dy = fa.STATION_D[k]
        print(f'arm{k} [{a.colors[k]:5s}] detected at world ({xy[0]:.3f}, {xy[1]:.3f}) '
              f'station-frame ({xy[0] - dx:.3f}, {xy[1] - dy:.3f})', flush=True)
        objs.append(xy)
    fa.run(model, a, out_name='color_sort', objs=objs,
           verify_bodies=[f'sort{k}_block' for k in range(4)])


if __name__ == '__main__':
    main()
