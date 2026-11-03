#!/usr/bin/env python3
"""示例入口：动作串烧秀（真机）。

实际实现在 ../src/python/robot/run2_moves_show_real_robot.py；本脚本切换工作目录后转交执行，
参数原样传递。真机执行示例：
    python run2_moves_show_real_robot.py show --robot device_A.json \
           --send --duration 5 --output results/show_hw.json
查看节目单：
    python run2_moves_show_real_robot.py list --duration 5
"""
import os
import sys
from pathlib import Path

target = (Path(__file__).resolve().parent.parent / "src" / "python"
          / "robot" / "run2_moves_show_real_robot.py")
os.chdir(target.parent)  # 结果输出到 robot/results/
Path("results").mkdir(exist_ok=True)
os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
