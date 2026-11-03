#!/usr/bin/env python3
"""示例入口：视觉颜色分拣（纯仿真）。

实际实现在 ../src/python/vision/demo_color_sort.py；本脚本切换工作目录后转交执行，
参数原样传递。用法示例：
    python demo_color_sort.py --show --video
    python demo_color_sort.py --colors blue green red green --seed 3 --video
"""
import os
import sys
from pathlib import Path

target = (Path(__file__).resolve().parent.parent / "src" / "python"
          / "vision" / "demo_color_sort.py")
os.chdir(target.parent)  # 结果输出到 vision/results/，模型按脚本目录解析
for d in ("results", "scenes"):  # 首次运行自动创建输出目录
    Path(d).mkdir(exist_ok=True)
os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
