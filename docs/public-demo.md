# LISS 部署与维护

LISS 是访客优先、可选账号的持久化写作与交流站点。内部配置名 `public-demo` 保留兼容；正式入口 `/welcome`，旧 `/demo` 重定向。文章、评论、资料与关注关系不会因 Cookie 过期、重启或普通代码更新自动删除。

**[线上站点](https://liss.pythonanywhere.com/)** · **[GitHub](https://github.com/heroliss/liss-web)**。2026-10-24 已在 PythonAnywhere 免费 Web app / Manual / Python 3.13 上线原持久访客版本，完成 HTTPS、真实发文、预览、评论与 Reload 保留检查。本次账号 / 界面升级正在本地联调，不将本地结果冒称已发布。

## 访客与账号

点击「开始写作」即可创建当前浏览器的独立访客身份。访客可真实发布、编辑自己的文章、回复、关注和填写个人资料。可选注册只需用户名、8–128 字符密码、确认密码与可选昵称，不要求邮箱或邮件确认。

当前访客注册时直接升级原用户记录，ID、文章、评论、资料和关注都保留。已有账号可用用户名与密码在其他浏览器登录；登录仅切换身份，不合并其他访客内容。账号支持修改密码，公开站暂不支持邮箱确认、邮件找回、外部头像设置、管理和公共 API。

访客 Cookie 是 30 天滚动会话。清除 Cookie、无痕浏览、换设备或 Cookie 失效可能使原访客无法认回，但不删除原账户与内容；想在其他设备继续编辑可在身份有效时升级为账号。密码丢失暂无法自行找回，需保存好登录信息。

公开内容应适合公开分享。所有写入有 CSRF 与单 IP 适度节流；关注只能通过 POST 提交。访客和账号均为普通角色，不可改写固定作者或其他人的内容。

| 写入限制 | 访客 | 账号 |
|---|---:|---:|
| 文章数 | 100 | 1,000 |
| 评论数 | 500 | 5,000 |
| 关注作者数 | 200 | 1,000 |
| 单篇文章字数 | 8,000 | 20,000 |
| 单条评论字数 | 2,000 | 4,000 |
| 个人简介字数 | 500 | 1,500 |

请求体上限为 256 KiB；写入 60 次 / 分钟，新访客 20 次 / 分钟，账号操作 10 次 / 分钟。同一 IP 分动作计数，单 worker 内存限速。配额可在 `PublicDemoConfig` 中调整；达到限额仍可浏览和编辑已有文章，不回收内容。

## 数据与密钥

`LISS_DATA_DIR/public-demo.sqlite` 保存用户、内容、关系和身份代次；旧 `LIHOU_DEMO_DIR` 仍是兼容别名。未指定时使用 Git 忽略的 `instance/public-demo/`。首次只补充缺表与欢迎文章，不清空内容。

已有 PythonAnywhere 部署继续使用 `/home/liss/lihou-demo-data/public-demo.sqlite` 与 `/home/liss/.config/lihou-web-demo.json`。这些历史内部路径、`lihou_demo` Cookie 名和 session 键无需随品牌改名；保留它们可避免误读空库或令旧身份失效。源码目录可以单独改名，但应停写、先备份，再修改 WSGI 路径；**不要把持久目录当作源码一起删除或重新初始化**。

`SECRET_KEY` 至少 32 字符，首次随机生成后安全保存。更新时读取同一密钥，不重复生成；更换密钥不会删内容，但原 Cookie 会失效。`public_demo_state.identity_epoch` 保留旧身份代次；历史 `epoch.json` 仅用于兼容，过期或丢失不触发清空。旧 `LIHOU_DEMO_RESET_SECONDS` 不再生效，清空命令已移除。

旧数据库首次升级先生成 `before-accounts-*.sqlite` 一致快照，再原地添加 `account_kind` 字段。仅将符合旧自动生成格式的访客标记为 guest，固定作者标记为 system，其余为 account。身份判断依据持久字段而非用户名。固定欢迎文章仅精确匹配旧种子原文才替换，先生成 `before-welcome-*.sqlite`，文章 ID 和回复关系不变，修改过的文章及任何访客内容保留。

| 环境变量（新名优先） | 默认 | 达到上限 |
|---|---|---|
| `LISS_MAX_IDENTITIES` / `LIHOU_DEMO_MAX_VISITORS` | 2,000 个访客与账号身份 | 暂停创建新身份；原访客仍可升级、原账号仍可登录 |
| `LISS_MAX_STORAGE_MIB` / `LIHOU_DEMO_MAX_STORAGE_MIB` | SQLite + WAL / journal 共 128 MiB | 暂停内容写入，保留内容 |

容量不会自动回收。维护者确认空间后可调整并 Reload。继续使用 1 个 WSGI worker，文件锁协调请求与备份 / 恢复；不需要后台保活或计划任务。

## PythonAnywhere 免费 WSGI

官方免费配置包含 1 个 Web app、1 个 worker、512 MiB 账户空间及 1 个月网站有效期；SQLite 对所有账户开放。适合低流量小站，并需每月检查控制台有效期。平台当前资格、地区及服务政策以实际控制台为准。

全新安装（已有部署直接按后文更新，不生成新密钥 / 新空库）：

```bash
git clone https://github.com/heroliss/liss-web.git
cd liss-web
mkvirtualenv --python=python3.13 liss-web
pip install -r requirements/public-demo.txt
```

首次只在私有目录创建一次配置文件；为兼容现有管理脚本，继续保留内部文件名：

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

文件已存在时停止即可，不覆盖、重新生成或公开输出密钥。

1. Web 添加应用，选择 **Manual configuration / Python 3.13**。
2. Source code 指向实际仓库目录，Virtualenv 指向相同 Python 3.13 环境。
3. 复制 [WSGI 样例](../deploy/pythonanywhere_wsgi.py)，替换账号名；源码目录为 `liss-web`，不存在新目录时样例会兼容现有旧目录。确认私有 JSON 与已有数据库位置正确。
4. Reload 后检查 `/healthz` 与首页；进入 `/welcome` 写作，并验证注册、登录、同 ID 升级和 Reload 后保留。
5. 开启 **Force HTTPS** 并 Reload，核对 HTTP 跳转与 HTTPS Cookie。
6. 每月检查 Web 有效期，按平台提示续期。

WSGI 保留服务器 `REMOTE_ADDR`；只有明确代理合同后才调整 `LIHOU_PROXY_HOPS`。本机 HTTP 预览可设 `LIHOU_DEMO_COOKIE_SECURE=0`，公网保持默认 HTTPS Cookie。

官方资料：[免费账户](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/)、[2026 调整](https://blog.pythonanywhere.com/221/)、[Flask](https://help.pythonanywhere.com/pages/Flask/)、[SQLite](https://help.pythonanywhere.com/pages/KindsOfDatabases/)。

## 一致备份与显式恢复

运行中不能直接复制 SQLite 主文件而忽略 WAL。管理命令读取与 WSGI 相同的私有密钥、持久目录，用 **SQLite backup API** 创建一致快照并核验结构与 `quick_check`。

```bash
cd ~/liss-web  # 仍保留旧源码目录时使用实际路径
workon liss-web  # 已有环境继续用原 virtualenv 名称
python deploy/manage_public.py backup-public-demo --output ~/liss-backups/2026-10-24.sqlite
```

拒绝覆盖已有输出；将备份下载到自己的存储，另行安全保存私有配置。快照含用户、密码散列、内容、关系及身份代次，不含 SECRET_KEY，按私有数据保存。

PythonAnywhere 的 512 MiB 包括仓库、环境、数据库、日志和备份。128 MiB 是应用写入软限制，不能测量整账户余量。自动迁移前的安全备份也占空间；应定期下载备份并核对配额，不自动删除任何快照。

只有明确需要回到某个快照才执行：

```bash
python deploy/manage_public.py restore-public-demo --input ~/liss-backups/2026-10-24.sqlite --yes
```

**恢复会替换当前内容，快照之后的数据不在恢复结果中。** 先自动生成 `before-restore-*.sqlite`，再准备新身份代次并一次事务写回；非法输入或准备失败不替换原库。操作需要足够空间容纳原库、安全备份与临时快照，建议在维护窗口执行。

显式恢复会撤销旧 Cookie，防止快照用户 ID 重用造成越权；快照中的注册账号可以重新登录，访客内容保留。恢复不是普通更新步骤。

日常更新：一致备份 → `git pull --ff-only` → 安装依赖 → Reload → 验收。保留同一个数据目录与密钥。管理脚本支持 `--settings /私有配置.json --data-dir /持久目录`（子命令前），不打印密钥。

## Docker 与验证

Render 免费文件系统在休眠、重启、部署后不可靠，不满足这次持久数据要求。[官方说明](https://render.com/docs/free)

`Dockerfile` 提供单 worker、非 root、固定密钥 + 持久卷的自托管入口，历史容器数据位置 `/data/lihou` 保持兼容：

```bash
docker build -t liss-web .
docker run --rm -p 7860:7860 -e SECRET_KEY --mount source=liss-data,target=/data/lihou liss-web
```

已有命名卷继续使用原卷名。Docker 镜像本机未实测；不要将无持久卷容器作为长期存储。

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

测试使用独立临时目录与测试密钥，覆盖旧数据 / Cookie 迁移、同 ID 升级、账号登录与普通权限、跨重启、原 TTL 之后保留、丢 Cookie 不删内容、CSRF / 越权 / 配额，以及 WAL 一致快照与显式恢复。普通模式仍保留邮件账户体系。
