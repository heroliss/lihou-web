# lihou-web

独立开发的 Flask 社交博客，最初于 2016 年运行在 Heroku。包含账户注册与邮件确认、登录、用户资料、角色权限、Markdown 发布与预览、评论、关注时间线、管理功能和 REST API。

2026 年对运行环境做了兼容升级。保留原页面样式与功能结构，使用 Python 3.14、Flask 3.1 与 SQLAlchemy 2.1。旧 Heroku 数据没有找回；演示内容是单独创建的示例。

## 本地演示（Windows PowerShell）

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:FLASK_CONFIG = 'demo'
.venv\Scripts\python manage.py seed-demo
.venv\Scripts\python manage.py runserver --port 5000
```

打开 http://127.0.0.1:5000 。示例账户为 `demo@example.com`，密码为 `local-demo-only`，只用于独立本地演示。

- demo 模式只使用 `data-demo.sqlite`，明确显示示例数据标识，并禁止发送邮件。
- `seed-demo` 可重复执行，不清空数据；在其它配置中会拒绝运行。
- 正常开发使用 development 配置和 `DEV_DATABASE_URL`；空数据库执行 `python manage.py deploy` 运行原迁移链并初始化角色。demo 的 seed 命令直接创建新样例结构，无需再执行 deploy。
- macOS / Linux 使用 `.venv/bin/python`，设置环境变量使用 `export FLASK_CONFIG=demo`。
- 可通过 `flask --app manage:app` 使用 Flask 原生命令；原 `manage.py runserver` 保留为本地开发别名。

## 验证

```powershell
.venv\Scripts\python -m pip install -r requirements/dev.txt
.venv\Scripts\python manage.py test
```

本次 Windows / Python 3.14 验证：38 项测试中 37 项通过，1 项历史 Selenium 测试默认跳过；另在当前浏览器中完成登录、发布、Markdown 预览、资料页与评论持久化检查。全新 SQLite 上的 11 个迁移步骤已运行至 `20261023_hash`。

历史 Selenium 脚本属于旧版浏览器自动化资料，默认不启动浏览器；当前交互验收单独记录。现代 Flask / Python 依赖经过安装兼容检查，精确环境快照见 `requirements/verified-python314.txt`。

## 兼容升级

- Flask-Script 改为 Flask / Click CLI，Werkzeug 中间件使用现代入口；分页与查询记录适配 Flask-SQLAlchemy 3。
- 使用现代 itsdangerous 的带过期时间签名令牌，旧 JWT 令牌不再兼容，需要重新申请确认或重置链接。
- 表单操作显式提交数据库事务，防止请求结束后回滚；拒绝匿名发评论与站外登录重定向。
- 适配 Bleach CSSSanitizer、WTForms DataRequired、Faker，修复假评论生成器未保存 Comment 的问题。
- 密码散列字段扩为 256 字符以容纳现代 Werkzeug 格式，保留旧密码验证能力。
- 保留历史 naive UTC 数据约定，并用现代 UTC 时钟生成时间；修正头像显示尺寸与 Markdown 代码块渲染。
- 移除早期百度主动推送脚本，demo 使用本地图标，Bootstrap 静态资源本地提供。

## 目录

`app/` 是页面、模型、鉴权与 REST API；`migrations/` 保留完整迁移历史；`tests/` 是模型与 HTTP 回归；`manage.py` 是命令入口。

远端部署仍需单独配置 SECRET_KEY、数据库与邮件服务。`requirements/heroku.txt` 提供当前 Gunicorn / psycopg 3 可选依赖，支持将 Heroku 形式的 PostgreSQL URL 转为 psycopg 3 驱动；本次没有重新部署到 Heroku，也没有验证生产 PostgreSQL 或真实邮件投递。
