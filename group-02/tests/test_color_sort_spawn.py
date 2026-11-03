"""Color-sort spawn-zone unit tests (no rendering, no simulation).

Run from anywhere:  python -m unittest discover -s tests -v
(在 .venv-vision 环境下)
"""
import sys
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "python" / "vision"))

from demo_color_sort import color_sort_elems, SPAWN, MIN_DIST   # noqa: E402
from demo_four_arms import STATION_D, OBJ0, PLACE0              # noqa: E402


class TestColorSortSpawn(unittest.TestCase):
    def setUp(self):
        self.elems = color_sort_elems(['blue', 'green', 'red', 'green'], seed=7)
        self.blocks = [e for e in self.elems if e.tag == 'body']
        self.cams = [e for e in self.elems if e.tag == 'camera']

    def test_element_count(self):
        self.assertEqual(len(self.blocks), 4)
        self.assertEqual(len(self.cams), 4)

    def test_blocks_inside_spawn_zone(self):
        for k, b in enumerate(self.blocks):
            x, y, _ = map(float, b.get('pos').split())
            dx, dy = STATION_D[k]
            sx, sy = x - dx, y - dy
            self.assertTrue(SPAWN[0] <= sx <= SPAWN[1], f'station{k} x={sx}')
            self.assertTrue(SPAWN[2] <= sy <= SPAWN[3], f'station{k} y={sy}')

    def test_blocks_keep_min_distance(self):
        for k, b in enumerate(self.blocks):
            x, y, _ = map(float, b.get('pos').split())
            dx, dy = STATION_D[k]
            d_red = np.hypot(x - (OBJ0[0] + dx), y - (OBJ0[1] + dy))
            d_pl = np.hypot(x - (PLACE0[0] + dx), y - (PLACE0[1] + dy))
            self.assertGreaterEqual(d_red, MIN_DIST, f'station{k} too close to distractor')
            self.assertGreaterEqual(d_pl, MIN_DIST, f'station{k} too close to place marker')

    def test_colors_match_request(self):
        rgba_by_name = {'blue': '0 .25 1 1', 'green': '0 .85 .12 1', 'red': '.9 .015 .01 1'}
        for b, color in zip(self.blocks, ['blue', 'green', 'red', 'green']):
            geom = b.find('geom')
            self.assertEqual(geom.get('rgba'), rgba_by_name[color])
            self.assertIsNotNone(b.find('freejoint'))


if __name__ == '__main__':
    unittest.main()
