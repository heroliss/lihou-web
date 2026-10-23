# 公开在线演示

`public-demo` 是独立、可重置的体验空间，不使用开发数据库、旧 Heroku 数据或真实邮箱。普通 `development` / `production` / 本地 `demo` 模式仍保留。

## 能体验什么

- 点击「进入演示」为当前浏览器创建临时普通账户，不使用共享密码。
- 发布 / 编辑自己的 Markdown 文章、评论示例文章、编辑资料、关注与取消关注。固定示例和其他访客的文章不能被覆盖。
- 不开放注册、密码 / 邮箱修改、管理页面和 REST API。页面按钮与真实路由均限制，不能靠直接请求绕过。
- 表单保留 CSRF；公开演示中的关注操作改用 POST。请求体上限 16 KiB，文章 4,000 字、评论 1,000 字、自我介绍 500 字。
- 每个临时账户最多 10 篇文章、20 条评论、10 位关注；每轮最多 80 位访客。默认同一来源每分钟最多进入 10 次、写入 20 次。

所有访客内容会公开显示。不要填写真实信息；这不是长期博客托管账户。

## 数据与重置

数据只写入 `LIHOU_DEMO_DIR/public-demo.sqlite`；未设置时使用仓库下被 Git 忽略的 `instance/public-demo/`。`DATABASE_URL` 不会把公开演示指向生产库。

第一次动态请求初始化示例。默认从上次初始化起满 24 小时后，下一次动态请求统一重置；没有计划任务，也不会为保持在线不断请求平台。SQLite、重置与 CLI 操作使用同一把进程间文件锁协调，重置代次变化使旧浏览器登录失效，即使数据库重新使用了相同用户 ID。

文件系统保留时，重启会保留本轮数据和代次；临时文件系统丢失时重建示例。不要承诺访客内容永久保存。

在已配置的服务器环境中可执行：

```bash
python manage.py prepare-public-demo
# 明确清除独立沙盒并使现有会话过期；不操作其他数据库
python manage.py reset-public-demo --yes
```

`seed-demo` 仍只接受本地 `demo`，不会给公开沙盒设置 README 中的本地示例密码。

WSGI 只使用 **1 个 worker**。文件锁协调数据操作，来源节流仍是单 worker 内存状态；这也是低流量免费演示的目标形态。不是面向多实例的生产社区部署。

## PythonAnywhere：免费 WSGI 候选

适合少量博客访问、希望账户文件存储保留、可以每月查看控制台有效期的情况。当前官方免费说明包含 1 个 Web app、1 个 worker、512 MiB 存储和 1 个月网站有效期；新免费账户取消的是 MySQL 和每日任务，仍可使用 SQLite。

以平台可选的 **Python 3.13** 为目标，操作在 PythonAnywhere 的 Bash console 中完成：

```bash
git clone https://github.com/heroliss/lihou-web.git
cd lihou-web
mkvirtualenv --python=python3.13 lihou-web-demo
pip install -r requirements/public-demo.txt
```

只在账户私有目录生成密钥，不把它粘到仓库或公开日志：

```bash
python - <<'PY'
import json
import os
from pathlib import Path
import secrets

private = Path.home() / '.config' / 'lihou-web-demo.json'
private.parent.mkdir(parents=True, exist_ok=True)
with private.open('x', encoding='utf-8') as output:
    json.dump({'SECRET_KEY': secrets.token_urlsafe(48)}, output)
os.chmod(private, 0o600)
PY
```

如果文件已经存在，该命令会停止；保留原密钥即可，不需要每次部署重新生成。

1. Web 控制台添加 Web app，选择 **Manual configuration / Python 3.13**，不要用 quickstart 覆盖已有源文件。
2. Source code 设置为 `/home/你的用户名/lihou-web`，Virtualenv 设置为 `/home/你的用户名/.virtualenvs/lihou-web-demo`。
3. 将 [WSGI 样例](../deploy/pythonanywhere_wsgi.py)复制到平台生成的 WSGI 配置中，将 `YOUR_USERNAME` 换为自己的用户名。
4. Reload，然后检查 `/healthz` 返回 `{"status":"ok"}`，再进入 `/demo` 体验发布和评论。
5. 每月检查 Web 控制台的有效期并按提示续期。没有测试过你的账号控制台，具体可选系统镜像与续期操作以平台为准。

样例使用 WSGI 提供的 `REMOTE_ADDR`，不直接信任用户转发首部。只有核验平台实际代理合同后，才调整 `LIHOU_PROXY_HOPS`。登录 Cookie 默认只经 HTTPS 发送。

官方资料：[免费账户](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/)、[2026 调整](https://blog.pythonanywhere.com/221/)、[Flask 接入](https://help.pythonanywhere.com/pages/Flask/)、[SQLite 支持](https://help.pythonanywhere.com/pages/KindsOfDatabases/)。

## Render：临时沙盒备选

仓库提供 [render.yaml](../render.yaml)，明确选择免费 Python 服务，构建安装 `requirements/public-demo.txt`，启动初始化后使用单 worker Gunicorn。平台自动生成 `SECRET_KEY`；绑定平台传入的 `PORT`，以 `/healthz` 做检查。

免费服务 15 分钟无流量后休眠，下次访问约需一分钟启动。部署、重启或休眠会丢失 SQLite 文件；对于明确会重置的演示可以接受。免费无支付方式账户超额时可能停止服务或停止构建，不保证一直可用。创建界面要确认 `Free` 和实际地区，不要选择付费实例或附加数据库。

不需要后台保活、外部数据库、邮件或付费磁盘。没有实际创建平台服务。

官方资料：[Flask 部署](https://render.com/docs/deploy-flask)、[免费限制](https://render.com/docs/free)、[账单 FAQ](https://render.com/docs/faq)。

## 平台无关 Docker

`Dockerfile` 提供非 root 用户、独立临时目录和单 worker Gunicorn，默认端口 7860。`SECRET_KEY` 必须由平台 secret / 本机环境提供；代码不会为公开模式使用内置默认密钥。

```bash
# 先设置 SECRET_KEY 环境变量，值至少 32 字符
docker build -t lihou-web-demo .
docker run --rm -p 7860:7860 -e SECRET_KEY lihou-web-demo
```

只有需要在本机 HTTP 预览时，另传 `-e LIHOU_DEMO_COOKIE_SECURE=0`；公网部署保持默认 HTTPS Cookie。其他平台的账号、计费和创建资格仍需单独核对。Docker 适配不等于已经完成镜像构建或该平台实机验收。

## 本地检查

```powershell
# 普通功能与公开模式一起检查，所有新测试使用临时目录
.venv\Scripts\python -m unittest discover -s tests -v
```

公开演示模式在启动时强制检查随机 `SECRET_KEY`，但测试用固定的测试密钥完全隔离，不能拿来部署。测试覆盖真实路由、CSRF、访客隔离、内容 / 频率限制、重启保留、过期重置与旧会话失效。Python 3.13 / 3.14 的本地验证不代表已在 PythonAnywhere、Render 或 Linux 容器中部署。
