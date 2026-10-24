# 网站管理

管理员使用原来的社区账号登录，进入 `/admin/`，也可从右上角账户菜单选择“网站管理”。访客和普通账号不能访问后台。

## 管理内容

- **概览**：文章、评论、用户状态和最近管理记录。
- **文章**：搜索作者、内容或编号，预览、编辑正文、隐藏与恢复，保留文章 ID 和项目关联。
- **评论**：结合文章上下文查找评论，隐藏或恢复；前台列表、计数与分页同步过滤。
- **用户**：查看身份、角色与状态，调整注册账号角色，停用或恢复身份。站点作者是可正常登录的注册管理员，拥有独立作者标记；管理界面不能将其停用或降级。访客不能提升为管理角色，最后一位有效管理员不能被降级，当前管理员不能停用自己。
- **网站设置**：站名、简介、欢迎语、注册开关、访客发文开关及访客文章上限。注册账号仍不限篇数；关闭访客发文不影响其评论和编辑旧文。
- **操作记录**：保留操作人、目标、时间及状态变更，不记录密码、邮箱、正文或服务密钥。

管理变更采用“预览 → 确认”两步，表单保留 CSRF 校验。确认在十分钟内有效，绑定当前会话与记录状态，服务器回执保证只能使用一次。隐藏可以恢复；不提供永久清空内容的操作。

## 首位管理员

先由本人在社区注册并确认实际用户名。维护者使用原部署环境执行：

```bash
python deploy/manage_public.py grant-admin --username YOUR_USERNAME
```

也可改用 `--user-id` 指定唯一用户 ID。命令只接受已存在、启用中的注册账号，按精确用户名匹配，记录授权操作，不修改密码；重复授权不会创建重复记录。它不会把访客或系统作者提升为管理员。

## 合并既有站点作者

旧站初始化时使用的系统内容身份 `demo` 不再需要作为另一个公开作者。维护者可将它的文章、评论与关注关系合并到自己的真实账号：保留真实账号 ID、密码、邮箱、验证状态和现有会话，统一用户名与显示名为 `Liss`，赋予管理员权限及独立的站点作者标记。

先备份并使用已有私有配置准备增量字段，然后规划与应用：

```sh
python deploy/manage_public.py backup-public-demo --output ~/liss-backups/before-owner.sqlite
python deploy/manage_public.py prepare-public-demo
python deploy/manage_public.py merge-site-owner --username lissbliss --new-username Liss --dry-run
python deploy/manage_public.py merge-site-owner --username lissbliss --new-username Liss --apply
```

不带 `--apply` 时只读取计划，不写数据库、不创建备份；数据库必须已完成 `prepare-public-demo` 的增量升级。实际应用前额外生成 `before-owner-merge-*.sqlite` 一致快照，所有归属转移在一个事务中完成。遇到同名身份、别人的作者标记或异常来源时拒绝合并；重复应用返回 `already-merged`，不再新增审计或备份。

合并后使用 `Liss` 和原账号密码登录，登录名不区分大小写，原来的 `lissbliss` 仍作为登录别名。规范主页为 `/user/Liss`，旧 `/user/demo`、`/user/lissbliss`、小写 `/user/liss` 及关注列表永久跳转到规范地址，旧名字不能被新用户注册。历史系统行仅作为停用的数据库归属记录保留，不展示在管理用户列表、不支持登录；它的文章已由真实作者持有，文章链接、正文、评论与项目关联不变。历史管理记录原样保留，未完成的管理确认全部作废。

站点作者可正常编辑介绍、指南和自己的文章。普通重载不再重新生成或覆盖初始作者内容；“站点作者”和“管理员”是独立概念，授权其他管理员不会自动授予作者标记。

托管站点仍读取原私有配置文件与数据目录，不需要新建管理员密码或更换 `SECRET_KEY`。管理服务的邮件密钥、数据库路径等不会暴露为可编辑的站点设置。

## 升级已有数据库

正式更新先运行 `backup-public-demo` 创建一致快照，然后快进源码、运行 `prepare-public-demo` 并 Reload。公共模式会在首次新增列前建立 `before-admin-*.sqlite` 备份；增加隐藏、停用、会话版本、设置和管理记录，保留既有正文、密码散列、身份与关系。重复准备不会重置设置或覆盖管理状态。

普通 Flask / Alembic 部署使用 `python manage.py db upgrade`。`20261024_admin_console` 同时兼容尚未升级的旧表和已由公共模式准备的数据库，不重复添加列。

停用会撤销已有会话；恢复不会重新激活旧 Cookie 或旧邮件令牌，注册用户可重新登录。修改邮箱、改密和密码找回继续由原账号流程处理。

## 验证

```bash
python -m unittest tests.test_admin_backend tests.test_admin_independent -v
python -m unittest discover -s tests -v
```

测试使用独立临时 SQLite，不操作线上数据。旧 `/moderate`、管理员资料编辑旁路和公共 REST API 继续关闭，新后台独立实施授权和审计。
