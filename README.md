# TRON2 课程单仓库协作管理系统

本项目为 TRON2 课程维护**唯一一个**公开 GitHub 仓库：`sherlver-h/tron2-course-materials`。35 名学生按 6 个小组在同一个仓库中协作，全部通过 GitHub Collaborators 获得仓库级 `push`（写入）权限。

本系统不会创建 6 个小组仓库、35 个学生仓库、模板仓库或 GitHub Organization，也不实现不存在的“通用邀请码”。`group-01/` 至 `group-06/` 是提交约定，不是 GitHub 权限隔离；每位协作者都能写整个仓库。

> 安全状态：所有会创建仓库、推送代码或发送邀请的操作都要求显式添加 `-Apply`。不带 `-Apply` 时是 Dry-Run。当前 `students.csv` 已预置 35 行姓名和分组，但 GitHub 用户名为空；请用真实名单填写后再部署。脚本会拒绝空值、格式错误、重复账号和占位账号。

## 1. Windows 环境准备

建议使用 Windows 10/11 和 PowerShell 7，也兼容 Windows PowerShell 5.1。打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

如果组织策略禁止修改执行策略，可在每次命令中使用 `powershell -ExecutionPolicy Bypass -File ...`，不要关闭系统级安全策略。

## 2. 安装 Git

```powershell
winget install --id Git.Git -e
git --version
git config --global user.name "你的姓名"
git config --global user.email "你的 GitHub 邮箱"
```

安装后重新打开 PowerShell。

## 3. 安装 GitHub CLI

```powershell
winget install --id GitHub.cli -e
gh --version
```

## 4. 登录 GitHub

必须登录固定仓库所有者 `sherlver-h`：

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth status
gh api user --jq .login
```

最后一条命令必须输出 `sherlver-h`。Token 至少需要个人仓库管理所需权限；不要把 Token 写入脚本或 CSV。

## 5. 创建唯一课程仓库

先进行只读环境和计划检查：

```powershell
Set-Location D:\tron
.\Initialize-Repository.ps1
```

确认输出中只有 `sherlver-h/tron2-course-materials` 后正式部署：

```powershell
.\Initialize-Repository.ps1 -Apply
```

脚本将创建唯一的 Public 仓库、提交当前课程结构、推送 `main`，并把默认分支设为 `main`。如果远程仓库已经存在，脚本立即停止，绝不覆盖原代码。如果本地 `origin` 指向别的仓库，也会停止。

## 6. 填写 CSV 学生名单

使用 Excel 或文本编辑器编辑 UTF-8 文件 `students.csv`：

```csv
group_id,student_name,github_username
01,张三,zhangsan-github
01,李四,lisi-github
```

必须满足：恰好 35 人；小组为 `01` 至 `06`；用户名真实存在且不重复；每人仅一行；不能使用 `github_user_01`、`student01`、`TODO` 等占位值。保存 CSV 时选择 **CSV UTF-8**，以保护中文姓名。

此仓库是 Public；把真实姓名提交到 GitHub 前，请确认已取得必要授权并符合学校隐私政策。如果名单不应公开，可在首次部署前由教师将 `students.csv` 加入 `.gitignore`，仅在本机维护，但脚本路径仍保持不变。

## 7. 批量邀请 35 名学生

Dry-Run 会验证 CSV、真实 GitHub 账号、仓库、已有协作者和待接受邀请，并生成报告，但不会发送邀请：

```powershell
.\Invite-Students.ps1
```

确认计划后正式邀请：

```powershell
.\Invite-Students.ps1 -Apply
```

脚本只给尚未加入且没有待接受邀请的账号发送 `push` 权限邀请；不会授予 Admin。可以中断后重跑，已有协作者和已有邀请会被幂等跳过。真实账号存在性检查不能绕过；如果 GitHub API 临时限流，请稍后重试。

## 8. 学生接受邀请

学生登录名单中的 GitHub 账号，打开 GitHub 通知或仓库邀请邮件，接受 `sherlver-h/tron2-course-materials` 的 Collaborator 邀请。待接受状态仍不能推送。GitHub 邀请可能过期，过期后教师需在 GitHub 页面撤销旧邀请，再重跑邀请脚本。

教师检查全部状态：

```powershell
.\Verify-Access.ps1
```

验证脚本只进行远程读取，不修改仓库。它会检查仓库所有者、Public 可见性、六组目录、35 人是否已邀请/已接受，以及已接受学生是否具有仓库级写入权限。

## 9. 学生克隆仓库

```powershell
git clone https://github.com/sherlver-h/tron2-course-materials.git
Set-Location tron2-course-materials
git config user.name "学生姓名"
git config user.email "学生的 GitHub 邮箱"
```

公开仓库可以匿名克隆，但只有已接受邀请且正确登录 GitHub 的账号才能推送。

## 10. 学生提交代码

以第 01 组为例：

```powershell
git pull --rebase origin main
# 只修改 group-01 中的文件
git add group-01
git commit -m "group-01: 完成传感器读取模块"
git push origin main
```

不要强制推送共享 `main`。虽然不要求 Pull Request，仍应先拉取、测试，再推送。详细规则见 `CONTRIBUTING.md` 和 `docs/submission-guide.md`。

## 11. 教师查看提交记录

```powershell
git fetch origin
git log --all --date=short --pretty=format:"%h %ad %an %s"
git log -- group-01
gh repo view sherlver-h/tron2-course-materials --web
```

按目录查看记录只是一种统计方式，不代表目录权限隔离。

## 12. 增加或移除学生

课程固定要求 35 人，因此替换学生时应在 `students.csv` 中修改对应一行并保持总数 35，然后先 Dry-Run、再 `-Apply`。新增名单账号不会影响现有协作者。

移除学生是破坏性权限操作，本工具不自动执行。教师需明确确认后运行：

```powershell
gh api --method DELETE repos/sherlver-h/tron2-course-materials/collaborators/真实用户名
```

随后更新 `students.csv` 并运行 `Verify-Access.ps1`。移除协作者不会删除其历史提交。

## 13. 检查邀请状态和报告

```powershell
.\Invite-Students.ps1       # 幂等 Dry-Run，显示已加入/待接受/待邀请
.\Verify-Access.ps1         # 要求全部接受且具有写权限
```

每次邀请或验证会更新：

- `reports/deployment-report.csv`
- `reports/deployment-report.json`
- `reports/deployment.log`

报告记录仓库、可见性、姓名、用户名、小组、邀请状态、是否接受、写权限、错误和北京时间/本机时区部署时间。`InvitationSent` 只表示邀请已发出；学生接受后再次验证才会变成 `Collaborator` 与 `has_write_access=true`。

## 14. 常见问题

**提示未安装 gh**：执行 `winget install --id GitHub.cli -e`，重开 PowerShell。

**当前账号不是 sherlver-h**：执行 `gh auth logout --hostname github.com`，然后重新 `gh auth login`。

**名单校验失败**：检查是否恰好 35 行、小组是否带前导零、用户名是否为空/重复/占位。脚本会逐行指出问题。

**账号格式正确但不存在**：让学生提供浏览器地址栏中真实的 GitHub 登录名；显示昵称不是用户名。

**学生收不到邀请**：先运行 Dry-Run 查看是否为 `PendingInvitation`。让学生检查 GitHub Notifications 和注册邮箱；必要时在仓库 Settings → Collaborators 中撤销过期邀请后重试。

**学生能改其他组目录**：这是预期行为。GitHub 个人仓库 Collaborator 权限是仓库级，不支持按 `group-XX/` 隔离。通过课程规范和提交记录管理，不要虚构文件夹级权限。

**推送冲突**：运行 `git pull --rebase origin main`，解决冲突并重新测试。不要 `git push --force`。

**远程仓库已存在**：初始化脚本会拒绝覆盖。先人工检查现有仓库；之后只运行邀请与验证脚本，不要再次初始化。

## 本地自动化测试

测试全部使用内存 Mock，不会访问 GitHub，也不会真实邀请学生：

```powershell
.\tests\Run-Tests.ps1
```

测试覆盖 CSV/UTF-8、35 人、6 组、重复与占位账号、模拟账号检查、协作者/待邀请跳过、API 错误、Dry-Run 零远程写、报告生成、目录结构和 PowerShell 语法。

## 正式部署命令（汇总）

```powershell
Set-Location D:\tron
.\tests\Run-Tests.ps1
.\Initialize-Repository.ps1           # Dry-Run
.\Initialize-Repository.ps1 -Apply    # 唯一一次：创建并推送唯一公开仓库
.\Invite-Students.ps1                 # Dry-Run
.\Invite-Students.ps1 -Apply          # 邀请尚未加入的学生
.\Verify-Access.ps1                    # 学生接受后验证
```
