# 第 02 组

TRON2 双臂机器人课程项目：视觉颜色分拣仿真 + 真机舞蹈 / 动作串烧演示。

## 成员与分工

| 成员 | 分工 |
|---|---|
| （待填写） | （待填写） |

## 项目功能与当前进度

1. **视觉颜色分拣（纯仿真）**：MuJoCo 四臂（2×2 工位）场景中，每个工位俯视相机做 HSV 颜色分割，像素坐标反投影为世界坐标 xy，机械臂按检测位置规划路径抓取目标色块。详见 `docs/三脚本使用文档.md` 第 1 节。
2. **双臂街舞（真机）**：60 拍预编排舞蹈（MoveJ + 夹爪 + 头部动作），`--duration 5` 时全长 5 分钟。详见文档第 2 节。
3. **动作串烧秀（真机）**：11 个动作组成的 63 拍节目单（nod → wave → … → bow）。详见文档第 3 节。

进度：（待填写）

## 环境与依赖

| 部分 | Python 环境 | 依赖清单 |
|---|---|---|
| 视觉仿真 `src/python/vision/` | `.venv-vision` | `src/python/vision/requirements.txt` |
| 真机控制 `src/python/robot/` | `.venv-humble` | `src/python/robot/requirements.txt` |

依赖安装：`pip install -r <对应目录>/requirements.txt`。注意两个环境不要混用（`.venv-vision` 缺 `websocket-client`，`.venv-humble` 缺 `mujoco`）。

硬件：TRON2 A 双臂机器人（DACH_TRON2A），网线直连 `10.192.1.x` 网段，WebSocket 端口 5000。

## 运行方法

```bash
# 1) 视觉颜色分拣（纯仿真，无需真机）
cd group-02/src/python/vision
source <.venv-vision>/bin/activate
python demo_color_sort.py --show --video        # 或仅 --video 无头录像

# 2) 真机舞蹈
cd group-02/src/python/robot
source <.venv-humble>/bin/activate
python3 run2_dance_real_robot.py dance --send --robot device_A.json \
       --duration 5 --output results/dance_live.json

# 3) 真机动作串烧秀
python3 run2_moves_show_real_robot.py show --robot device_A.json \
       --send --duration 5 --output results/show_hw.json

# 两个真机脚本也支持 MuJoCo 仿真可视化（无需真机，需在 .venv-vision 下运行）
python3 run2_dance_real_robot.py dance --sim --duration 3                 # 实时窗口
python3 run2_moves_show_real_robot.py show --sim --duration 3 \
       --sim-video results/show_sim.mp4                                  # 或输出 mp4
```

> 注意：仓库不提供真实 `device_A.json`（含内网 IP 与机器人 SN）。请复制
> `data/robot_profile.example.json` 为模板，填入真机 `url`、`accid`、关节名后使用。
> 真机命令执行前需人工输入 `y` 确认，且必须 `--duration >= 5`（profile 的 `min_segment_s`）。

完整参数说明、安全注意事项与故障排查见 `docs/三脚本使用文档.md`。

## 目录说明

```text
group-02/
├── README.md                  # 本文件
├── src/python/
│   ├── vision/                # 视觉分拣仿真（需同目录 official/ 机器人模型）
│   │   ├── demo_color_sort.py #   主脚本：HSV 定位 + 抓取
│   │   ├── demo_four_arms.py  #   四臂场景构建与运行框架
│   │   ├── demo_compare_paths.py / build_scene.py
│   │   └── official/          #   官方机器人 XML（meshes 按教师要求不进仓库，见下）
│   └── robot/                 # 真机 SDK 演示脚本
│       ├── run2_dance_real_robot.py / run2_moves_show_real_robot.py
│       ├── run2_moves_real_robot.py  # 动作库（被 show 脚本导入）
│       ├── client.py / lessons.py    # 通信客户端与基础课程函数
│       ├── mujoco_client.py          # --sim 仿真客户端（鸭子类型兼容 client.py，需 mujoco）
│       └── run.py                    # 仿真教学脚本（被 tests/test_contract.py 引用）
├── docs/                      # 设计说明与文档
│   ├── 三脚本使用文档.md      # 完整使用说明
│   └── ROS秋季学校xx_2组.pptx # 项目演示 PPT
├── data/robot_profile.example.json   # 真机 profile 模板（勿填真实 SN 后提交）
├── examples/                  # 演示入口（薄封装，chdir 后转交 src/python/ 下同名脚本）
│   ├── demo_color_sort.py
│   ├── run2_dance_real_robot.py
│   └── run2_moves_show_real_robot.py
└── tests/                     # 单元测试
    ├── test_contract.py           # 客户端/课程函数契约测试（mock WebSocket，.venv-humble）
    ├── test_color_sort_spawn.py   # 颜色分拣生成约束测试（.venv-vision）
    └── requirements-test.txt
```

## 测试方法与已知问题

```bash
# 在 .venv-humble 下运行全部测试（mock WebSocket 契约 20 + 视觉生成约束 4；
# .venv-vision 下则为 仿真客户端 3 + 视觉生成约束 4，按环境自动跳过缺依赖的项）
cd group-02
python -m unittest discover -s tests -v
# 首次运行先装测试依赖：pip install -r tests/requirements-test.txt
```

- 视觉仿真：运行日志打印各工位色块"生成位置 vs 检测位置"可对比验证；检测失败（`RuntimeError`）时换 `--seed` 重试。
- 真机：执行后检查 `results/*.json`（含每拍计数、总时长）与同名 `.events.json`（报文往返时间线）。
- 已知问题：（待填写）
