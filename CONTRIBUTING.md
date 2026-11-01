# TRON2 课程代码协作规范

本课程只使用仓库 `sherlver-h/tron2-course-materials`。所有学生都是同一仓库的 Collaborator，拥有整个仓库的写入权限；小组目录是协作约定，不是权限边界。

## 提交流程

1. 先执行 `git pull --rebase origin main`。
2. 只修改本组的 `group-XX/`；公共材料经教师同意后放入 `common/` 或 `docs/`。
3. 不提交密码、Token、个人隐私、编译产物、大型数据集或虚拟环境。
4. 运行本组测试后再提交，提交信息使用 `group-XX: 简要说明`。
5. 直接推送：`git push origin main`。本课程不强制 Pull Request 审核。

## 冲突与误修改

发生冲突时不要强制推送。先保存工作，联系相关同学共同解决。误改其他组文件时使用新的修复提交，不要重写共享分支历史。
