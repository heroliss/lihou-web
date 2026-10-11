# heroliss/lihou-web

## 项目用途

基于 Flask 的社交博客/论坛示例，包含注册、邮件确认、登录、角色权限、发帖、Markdown、评论、关注/时间线、头像、管理界面和 REST API。

## 当前状态

- 建议分类：老旧 Flask 社交博客/学习项目
- 生命周期：Dormant
- 最近 push：2016-12-23T08:11:15Z
- README 扫描状态：Missing

最新 push 为 2016-12-23，长期冻结。适合标为完成的 Flask 学习/部署示例；需要旧环境或容器才能复现。

## 实际运行方式

历史运行环境是 Python 3.5.2。创建旧版虚拟环境后安装 requirements/dev.txt，执行 python manage.py db upgrade、python manage.py deploy、python manage.py runserver；测试可执行 python manage.py test；生产入口为 gunicorn manage:app。当前 Flask 0.11、Flask-Script 和旧 Werkzeug 依赖已过时，现代 Python 大概率无法直接运行，且不建议直接公网部署。

## 依赖与构建线索

Python 3.5.2；Flask 0.11.1、SQLAlchemy 1.1.4 等老依赖；Heroku Procfile；仓库含 chromedriver.exe。邮件、数据库和 SECRET_KEY 依赖环境变量。

## 目录与证据

app、migrations、tests、manage.py、Procfile、runtime.txt 齐全；Procfile 为 web: gunicorn manage:app。

顶层目录/文件线索：

- `.gitignore`
- `app`
- `chromedriver.exe`
- `config.py`
- `manage.py`
- `migrations`
- `Procfile`
- `requirements`
- `requirements.txt`
- `runtime.txt`
- `tests`

## 已知限制

- 本文档只依据 GitHub 元数据、README、构建文件、入口文件和目录证据整理，未进行完整构建或端到端运行验证。
- 不确定的运行环境、密码、服务器地址和兼容版本不在此处臆测。

## 需要确认

是否需要保留为 Flask 学习成果？如果需要，建议另做“历史运行环境”说明，不升级代码。

## 后续维护建议

1. 先确认“继续维护、完成冻结、历史保留、归档候选”中的一种状态。
2. 若继续保留，再补充一条可复现的运行验证记录和截图/输出。
3. 若确认完成或废弃，只保留简短历史说明，不需要改代码。

## 证据链接

- 仓库：[heroliss/lihou-web](https://github.com/heroliss/lihou-web)
- 默认分支：main
- 主要语言：Python
- stars / forks：0 / 0
- 扫描时间：2026-10-11T05:10:08Z
## 本地验证记录

在 Python 3.14 临时副本中导入 `manage` 失败，原因是当前环境没有旧版 Flask；项目还锁定 Python 3.5.2 和一组已过时依赖，需要历史环境或容器才能复现。
