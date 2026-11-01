# 作业提交指南

```powershell
git clone https://github.com/sherlver-h/tron2-course-materials.git
cd tron2-course-materials
git pull --rebase origin main
# 修改自己的 group-XX 目录
git add group-XX
git commit -m "group-XX: 完成本次作业"
git push origin main
```

推送被拒绝时，通常是远程已有新提交。再次执行 `git pull --rebase origin main`，解决冲突并测试后再推送。禁止对共享 `main` 使用强制推送。
