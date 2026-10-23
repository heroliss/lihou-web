# 持久化的在线访客展示

`public-demo` 保留历史配置名，当前用途是**长期保留内容的在线展示**。文章、评论、资料和关注关系不会因时间、Cookie 过期、应用重启或普通代码更新被自动删除。它不使用开发数据库、旧 Heroku 数据或真实邮箱。

## 访客身份与内容

- 点击「进入演示」为当前浏览器创建普通访客身份，无需共享密码、邮箱或注册。
- 真实体验 Markdown 发布、编辑自己的文章、评论、资料和关注。固定示例与其他访客的文章不能被覆盖。
- 身份由签名 Cookie 识别，活跃访问延长 30 天会话。清除 Cookie、无痕窗口或身份过期后，原账户和内容仍保留，但不能保证恢复原身份继续编辑。这不是提供身份找回服务的个人博客账户。
- 所有提交内容会公开显示；请勿填写真实个人信息。注册、改密 / 邮箱、管理和 API 写入口均不开放，实际路由也有限制。
- 保留 CSRF、POST 关注、内容长度与频率限制：请求体 16 KiB，文章 4,000 字，评论 1,000 字，简介 500 字；每个访客最多 10 篇文章、20 条评论、10 位关注。

## 长期存储配置

`LIHOU_DEMO_DIR/public-demo.sqlite` 保存全部数据和浏览器身份代次。默认目录是被 Git 忽略的 `instance/public-demo/`。PythonAnywhere 样例使用 `/home/你的用户名/lihou-demo-data/`，与仓库、virtualenv 分开。更新代码时保留它，**不要把该目录放入临时文件系统**。

`SECRET_KEY` 也必须固定保留：启动时要求至少 32 字符的随机值，并拒绝使用内置默认密钥。同一个数据库搭配同一密钥，重启 / 更新后原 Cookie 可继续识别。更换密钥不会删除内容，但旧 Cookie 会失效。

首次运行只补充缺少的表和固定示例，不清空已有内容。旧版本 `epoch.json` 只用于一次性兼容迁移，过期或丢失也不会触发数据删除。`LIHOU_DEMO_RESET_SECONDS` 已不再生效；旧 `reset-public-demo` 命令已移除。

容量只限制新增，不回收内容：

| 环境变量 | 默认 | 到达上限后的行为 |
|---|---|---|
| `LIHOU_DEMO_MAX_VISITORS` | 2,000 位访客 | 暂停新增访客身份；原账户与内容继续保留 |
| `LIHOU_DEMO_MAX_STORAGE_MIB` | SQLite 主文件及 WAL / journal 合计 128 MiB | 暂停内容写入；浏览仍可用 |

维护者确认余量后可调整容量并 Reload。不会自动删用户、文章、评论或备份。来源节流为单 worker 内存状态，继续使用 **1 个 WSGI worker**；文件锁协调请求与备份 / 恢复，不需要计划任务或后台保活。

## PythonAnywhere 免费 WSGI

适合少量博客访问和愿意每月检查 Web 控制台有效期的情况。官方免费说明包含 1 个 Web app、1 个 worker、512 MiB 账户文件空间和 1 个月网站有效期；SQLite 对所有账户开放。平台账户与存储政策仍以实际控制台为准，应用本身不承诺平台永不改变服务。

在 PythonAnywhere 的 Bash console 中执行，以当前平台支持的 Python 3.13 为目标：

```bash
git clone https://github.com/heroliss/lihou-web.git
cd lihou-web
mkvirtualenv --python=python3.13 lihou-web-demo
pip install -r requirements/public-demo.txt
```

首次只在账户私有目录生成一次密钥：

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

文件已存在时命令会停止；保留原文件即可。不要在更新时重新生成密钥，也不要将它上传仓库或贴进公开日志。

1. Web 控制台添加 Web app，选择 **Manual configuration / Python 3.13**，不使用覆盖源文件的 quickstart。
2. Source code：`/home/你的用户名/lihou-web`；Virtualenv：`/home/你的用户名/.virtualenvs/lihou-web-demo`。
3. 将 [WSGI 样例](../deploy/pythonanywhere_wsgi.py)复制到平台生成的 WSGI 配置，将 `YOUR_USERNAME` 替换成实际用户名。
4. Reload，检查 `/healthz` 的 `{"status":"ok"}`，再在 `/demo` 进入、发布文章、评论、刷新和重载后核对。
5. 每月查看 Web 控制台有效期并按提示续期；不要把免费网站当作免维护存档服务。

WSGI 样例保留服务器提供的 `REMOTE_ADDR`。只有核验平台代理合同后才调整 `LIHOU_PROXY_HOPS`；公网 Cookie 默认只经 HTTPS 发送。

官方资料：[免费账户](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/)、[2026 调整](https://blog.pythonanywhere.com/221/)、[Flask 接入](https://help.pythonanywhere.com/pages/Flask/)、[SQLite 支持](https://help.pythonanywhere.com/pages/KindsOfDatabases/)。

## 一致备份与恢复

不要在运行时直接 `cp public-demo.sqlite`，尤其不能忽略未 checkpoint 的 WAL。下面的管理入口读取与 WSGI 相同的私有密钥及目录，用 **SQLite backup API** 生成一致快照，并核验数据库结构与 `quick_check`：

```bash
cd ~/lihou-web
workon lihou-web-demo
python deploy/manage_public.py backup-public-demo --output ~/lihou-backups/2026-10-24.sqlite
```

输出文件已存在时拒绝覆盖。下载备份到自己的存储，并**另行安全保存私有密钥 JSON**。数据库快照包含用户、文章、评论、关注和身份元数据，不包含仓库代码或 SECRET_KEY。

PythonAnywhere 的 512 MiB 配额还包括源码、virtualenv、SQLite、日志与备份。默认 128 MiB 是应用写入软限制，不是整账号配额测量；每个备份也占空间。定期下载备份并检查账户余量，不自动轮转或删除任何备份。

只有明确要将站点回到某个快照时才使用恢复：

```bash
python deploy/manage_public.py restore-public-demo --input ~/lihou-backups/2026-10-24.sqlite --yes
```

**恢复会替换当前数据，快照之后的新内容不在恢复结果中。** 命令先在数据目录生成 `before-restore-*.sqlite` 一致安全备份，再准备带有新身份代次的临时快照，一次 SQLite backup 事务写回；输入无效或准备失败时不会开始替换。恢复需要足够空间容纳原库、安全备份和临时快照，完成后仅删除本次临时文件，保留安全备份。必须提供 `--yes`，且会撤销旧浏览器登录，防止用户 ID 重用造成越权；快照内的账户和内容仍保留。建议在维护窗口执行，Reload 后再核对内容。恢复不是常规更新步骤。

日常更新：先备份，再 `git pull --ff-only`、安装依赖和 Reload。保留数据目录与同一私有密钥，不运行任何清空脚本。

若使用其他路径，管理入口支持 `--settings /私有配置.json --data-dir /持久目录`，放在子命令之前。

## 其他托管与 Docker

Render 免费服务的文件系统会在休眠、重启或部署时丢失写入，**不满足这次长期保留的要求**，因此移除了默认免费 blueprint。没有注册或创建任何收费资源。若另选平台，必须先确认持久存储与备份方案。[Render 官方限制](https://render.com/docs/free)

`Dockerfile` 使用非 root 用户和单 worker Gunicorn，数据目录为 `/data/lihou`。自行托管时使用**命名持久卷**与固定 SECRET_KEY：

```bash
# 先在当前环境提供固定 SECRET_KEY，至少 32 字符；不要每次启动更换
# 原卷必须保留，容器重新创建仍挂载同一个卷
docker build -t lihou-web-demo .
docker run --rm -p 7860:7860 -e SECRET_KEY --mount source=lihou-visitor-data,target=/data/lihou lihou-web-demo
```

只有本机 HTTP 预览另传 `-e LIHOU_DEMO_COOKIE_SECURE=0`；公网保持默认 HTTPS Cookie。没有实测 Docker 镜像或远端托管，配置文件不等于实机验收。

## 本地验证

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

测试使用独立临时目录和测试密钥，覆盖真实路由、CSRF、访客隔离、配额、超过原 TTL 仍保留、旧版本迁移、跨重启 / 丢 Cookie 的内容保留，以及包含已提交 WAL 的一致备份和显式恢复。固定示例不是旧 Heroku 恢复数据；普通开发 / 生产模式保持原行为。
