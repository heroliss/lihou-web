# Liss Community · 记录与交流

一个独立开发的 Flask 写作与交流网站。最初在 2016 年作为学习后端、前端和 Web 技术的实操项目，2026 年升级到 Python 3.13 / 3.14、Flask 3.1、SQLAlchemy 2.1，并重新设计为默认暗色、支持浅色切换的现代界面。

**[在线访问](https://liss.pythonanywhere.com/)** · **[源码](https://github.com/heroliss/liss-community)** · PythonAnywhere 免费托管 / HTTPS

无需注册即可用浏览器访客身份发文、编辑、评论、关注作者和完善资料。创建用户名与密码账号后，可在其他浏览器继续登录；从当前访客升级时保留同一用户 ID、全部文章、评论和关注关系。文章与身份长期存储，不会自动重置或清空。

站点作者使用可正常登录的真实账号，独立的作者标记与管理员权限共同展示身份。旧系统作者的文章、评论和关注可通过[有备份、可重复执行的合并命令](docs/admin.md#合并既有站点作者)归入同一个账号，保留原密码和邮箱；旧名字继续作为登录别名与主页跳转。

访客身份依靠当前浏览器的 Cookie；清除 Cookie、无痕浏览或换设备后可能无法认回，但已发布内容仍保留。邮件服务配置就绪后，提供可选邮箱绑定，支持验证邮件、重发、密码找回与绑定 / 更换邮箱。当前未配置真实发信时继续提供用户名注册，并明确提示；原用户名账号始终兼容。登录已有账号只切换身份，不自动合并当前访客内容。

## 作品与交流

作品展厅由 Astro 构建并部署到 [Liss Works](https://heroliss.github.io/liss-works/)，旧 `/works/` 入口重定向并保留路径。社区导航可以进入作品，20 项作品各自有 `/projects/<slug>/talk/` 讨论页；这里发表的讨论属于真实文章，可在文章流阅读、继续评论并由原作者编辑。原文章保留，关联表只记录项目关系。

## 网站管理

已授权管理员登录原账号后，可从账户菜单进入 `/admin/`。后台提供文章预览与编辑、内容隐藏与恢复、用户角色和停用管理、站点介绍与写作规则设置，以及操作记录。变更先预览再确认；隐藏保留原文，停用撤销已有会话，保护系统身份与最后一位管理员。

首次授权、数据库升级及测试方法见 [网站管理说明](docs/admin.md)。后台不提供服务密钥编辑，也不提供永久清空内容的操作。

邮箱验证 / 找回通过 Brevo 或 Resend 的 HTTPS API，凭据仅放服务器私有配置。用途独立的签名链接 1 小时有效；密码重置会撤销其他旧登录会话。已有用户名账号可登录后绑定邮箱；从访客升级继续复用原用户 ID。完整配置步骤与免费服务限制见 [部署指南](docs/public-demo.md)。没有真实发信前，不将测试 outbox 结果当作投递成功。

## 运行

使用 Python 3.13 或 3.14。在 Windows PowerShell 中：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:FLASK_CONFIG = 'public-demo'
$env:LISS_DATA_DIR = (Join-Path $PWD 'instance/public-demo')
# 首次生成并安全保存一个至少32字符的随机SECRET_KEY；以后使用同一个值
$env:SECRET_KEY = '替换为自己固定保存的随机密钥'
$env:LIHOU_DEMO_COOKIE_SECURE = '0' # 仅限本地HTTP预览
.venv\Scripts\python manage.py runserver --port 5000
```

打开 http://127.0.0.1:5000 。`public-demo` 是保留的内部配置名，产品界面使用 LISS；正式入口为 `/welcome`，旧 `/demo` 书签继续跳转。首次启动补充站点写作指南，后续启动保留原库及身份。不要更换密钥或指定新的空目录来更新网站。

PythonAnywhere 的 WSGI、现有存储升级、固定密钥、SQLite 一致备份与恢复见 **[部署与维护指南](docs/public-demo.md)**。已有部署的数据目录、私有配置与 Cookie 名保留兼容，仓库改名不会重置身份。公网使用 HTTPS 与正式 WSGI，继续保留 1 个 worker。

## 身份与边界

访客与注册账号都可发布、编辑自己的文章、评论、关注与维护资料。注册账号拥有更宽的写入配额并支持改密，访客无需邮箱或共享密码。普通身份不能覆盖其他作者的内容；新管理后台仅向有效的注册管理员开放，旧管理旁路和公共 REST API 继续关闭。所有写入口保留 CSRF 与适度限速，容量到限仅暂停新增或写入，不删除已有数据。

新版本在原 SQLite 中增加身份类型；迁移前自动生成一致备份，保留用户 ID、旧 Cookie 身份代次和关系。固定初始文章只有精确匹配未修改的旧种子内容时才更新为正式欢迎 / 写作说明，并先备份；用户内容不会被改写。旧 Heroku 数据没有找回，这次站点使用重新上线后积累的数据。

## 验证

```powershell
.venv\Scripts\python -m pip install -r requirements/dev.txt
.venv\Scripts\python -m unittest discover -s tests -v
```

回归覆盖模型、权限、真实表单路由、CSRF、访客隔离、账号注册 / 登录、同 ID 访客升级、旧 SQLite 与旧 Cookie 兼容、跨重启、配额、内容保留，以及包含已提交 WAL 的 SQLite backup API 备份与显式恢复。历史 Selenium 脚本默认跳过，实际浏览器交互另行验收。

PythonAnywhere / Python 3.13 的原访客版本已完成公网发文、Markdown 实时预览、评论与 Web Reload 后身份 / 内容保留验收；本次账号与界面升级先本地联调再更新线上。Docker 镜像仍未实测，真实 SMTP 与生产 PostgreSQL 不在这次验收范围内。

## 技术与兼容

- Flask Blueprint、Jinja、SQLAlchemy、Flask-Login / WTF、Markdown + Bleach、REST API 与角色权限，前端支持实时预览和深浅主题。
- Flask-Script 已迁移至 Flask / Click CLI，适配现代分页、查询记录、WTForms、Faker 与 Bleach CSSSanitizer。
- 使用带时效签名令牌，现代 Werkzeug 密码散列字段扩为 256 字符；保留历史密码验证和 naive UTC 数据约定。
- 访客和账号类型持久化，公开站使用可选 HTTPS 事务邮件，无 SMTP 依赖；普通开发 / 生产模式保留原邮件机制。
- `app/` 为应用与接口，`migrations/` 为原迁移链与新版增量，`tests/` 为回归测试，`manage.py` / `deploy/` 为管理和部署入口。

普通开发使用 `development` + `DEV_DATABASE_URL`，空库执行 `python manage.py deploy` 升级迁移并初始化角色。独立本地开发样例可使用 `demo`、`seed-demo` 和 `demo@example.com` / `local-demo-only`；该密码不适用于公开站。生产 / Heroku 模式需自行配置数据库、固定 SECRET_KEY 与邮件；`requirements/heroku.txt` 提供 Gunicorn / psycopg 3 可选依赖。
