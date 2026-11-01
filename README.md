# TRON2 课程小组代码仓库

本仓库用于 TRON2 课程的代码开发与小组协作。全班共同使用这一个仓库，课程项目按照 6 个小组分别存放在 `group-01/` 至 `group-06/` 中。

请每位同学只在自己小组的目录中添加或修改文件。小组目录是一项协作约定，并不是 GitHub 权限隔离；加入仓库后，成员可以看到并修改整个仓库，因此提交前务必确认文件路径和改动内容。

## 仓库目录

```text
tron2-course-materials/
├── README.md                 # 仓库总说明（本文件）
├── .gitignore               # Git 忽略规则
│
├── group-01/                # 第 01 组
├── group-02/                # 第 02 组
├── group-03/                # 第 03 组
├── group-04/                # 第 04 组
├── group-05/                # 第 05 组
└── group-06/                # 第 06 组
```

六个小组的内部结构完全相同：

```text
group-XX/
├── README.md                # 本组项目说明、成员分工和运行方法
├── src/
│   ├── python/              # Python 源代码
│   └── cpp/                 # C++ 源代码
├── include/                 # C/C++ 头文件
├── tests/                   # 测试代码
├── examples/                # 示例程序、演示代码
├── docs/                    # 本组设计文档、实验报告和使用说明
└── data/                    # 项目使用的小型、非敏感数据
```

其中 `XX` 是小组编号，例如第 03 组对应 `group-03/`。

## 文件应该放在哪里

| 内容 | 放置路径 | 常见文件示例 |
|---|---|---|
| Python 源代码 | `group-XX/src/python/` | `.py` |
| C++ 源代码 | `group-XX/src/cpp/` | `.cpp`、`.cc`、`.cxx` |
| C/C++ 头文件 | `group-XX/include/` | `.h`、`.hpp` |
| 单元测试和集成测试 | `group-XX/tests/` | `test_*.py`、`*_test.cpp` |
| 可运行示例和演示程序 | `group-XX/examples/` | 示例脚本、演示入口程序 |
| 设计说明和实验文档 | `group-XX/docs/` | `.md`、必要的 `.pdf` |
| 小型项目数据 | `group-XX/data/` | `.csv`、`.json`、配置样例 |
| 本组项目总说明 | `group-XX/README.md` | 项目介绍、成员分工、环境与运行命令 |

例如，第 02 组提交 Python 控制程序时，应放在：

```text
group-02/src/python/
```

第 05 组提交 C++ 头文件时，应放在：

```text
group-05/include/
```

不要把小组作业直接放在仓库根目录，也不要把文件放入其他小组的目录。

## 各小组入口

- [第 01 组](group-01/README.md)
- [第 02 组](group-02/README.md)
- [第 03 组](group-03/README.md)
- [第 04 组](group-04/README.md)
- [第 05 组](group-05/README.md)
- [第 06 组](group-06/README.md)

各组应继续完善自己的 `README.md`，至少写明：

1. 小组成员与分工。
2. 项目功能和当前进度。
3. 软件、依赖和硬件环境。
4. 安装、编译和运行方法。
5. 测试方法和已知问题。

## 学生首次使用

收到 GitHub Collaborator 邀请后，先登录自己的 GitHub 账号并接受邀请，然后克隆仓库：

```powershell
git clone https://github.com/sherlver-h/tron2-course-materials.git
Set-Location tron2-course-materials
```

首次提交前设置自己的姓名和 GitHub 邮箱：

```powershell
git config user.name "你的姓名"
git config user.email "你的 GitHub 邮箱"
```

## 日常提交步骤

以下以第 01 组为例。开始修改前先同步远程代码：

```powershell
git pull --rebase origin main
```

将文件放入第 01 组对应目录，然后只暂存本组内容：

```powershell
git add group-01
git status
```

确认 `git status` 中没有其他组的文件后提交并推送：

```powershell
git commit -m "group-01: 简要说明本次修改"
git push origin main
```

其他小组请把命令中的 `group-01` 替换成自己的小组编号。

## 协作规范

1. 只修改自己小组的 `group-XX/` 目录。
2. 每次工作前先执行 `git pull --rebase origin main`。
3. 提交前执行 `git status`，检查是否误改其他组文件。
4. 提交信息建议采用 `group-XX: 修改说明` 格式。
5. 禁止向共享的 `main` 分支执行强制推送，例如 `git push --force`。
6. 不要提交密码、Token、私钥、个人隐私或其他敏感信息。
7. 不要提交虚拟环境、缓存、编译产物和临时文件，例如 `.venv/`、`__pycache__/`、`build/` 和 `.exe`。
8. 大型数据集、模型文件和视频不要直接上传；先与教师确认存储方式。
9. 发生提交冲突时不要覆盖他人代码，应联系相关同学共同解决。

## 关于 `.gitkeep`

新建仓库时，Git 不会记录空目录，因此各目录中暂时放置了 `.gitkeep` 文件。它只用于保留目录结构，不是课程代码。同学添加真实文件后可以保留它，也可以删除所在目录中的 `.gitkeep`。

## 仓库地址

```text
https://github.com/sherlver-h/tron2-course-materials
```
