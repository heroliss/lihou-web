# 国内邮件接入准备：阿里云 DirectMail

核对日期：2026-10-24。当前只完成代码与隔离测试，**未开通云账号、购买域名、设置真实凭据或发送真实邮件**。线上邮件功能继续关闭；用户名注册、访客使用及已有内容不受影响。

## 选择与费用

DirectMail 提供中国杭州的 HTTPS API，适合邮箱确认、换绑及密码找回。它不是公共收件邮箱，也不能直接借用 QQ、126 或 `pythonanywhere.com` 的域名发信。

- 当前官方免费量为**每个主账户总计 2,000 封，每天最多免费 200 封**，用完即进入按量计费。当前按量单价为 2 元 / 1,000 封；**并非永久每天免费 200 封**。开通前再次核对控制台与账单规则。[官方计费](https://help.aliyun.com/zh/direct-mail/billing-methods/)
- 对照方案腾讯 SES 也是总计 1,000 封免费，用完按量收费，并非长期每月补充免费量。[腾讯官方说明](https://cloud.tencent.cn/document/product/1288/47930)
- 因此，在没有自有域名且优先零费用的条件下，此方案先保留待配置，不代表已经满足长期免费要求。

## 启用前仍需完成

1. 由账号所有者注册阿里云账号、完成实名认证，并自行确认开通条款和按量计费。代码不会代为注册或购买。[官方开通流程](https://help.aliyun.com/zh/direct-mail/purchase-procedure)
2. 准备有 DNS 管理权限的发信域名，建议使用单独邮件子域，避免干扰现有邮箱。网站仍可使用免费托管网址；发信域名不要求与网站域名相同。免费 PythonAnywhere 子域的 DNS 不属于站长，不能拿来完成发信域名验证。
3. 在目标地域的 DirectMail 控制台添加域名，按控制台给出的实际记录完成验证。当前新域名需 SPF、DKIM、DMARC、MX 等验证，**不要照抄别人的验证记录**，也不要覆盖其他邮箱的既有 MX / SPF。随后创建触发类发信地址。[域名配置](https://help.aliyun.com/zh/direct-mail/user-guide/how-to-configure-sending-domain-names)
4. 创建专用 RAM 身份，仅授权下面的发信动作；不使用主账号长期密钥。私有 AccessKey 只进入服务器权限受限的配置文件或环境变量，不贴到聊天、提交到 Git，或写进浏览器代码。[接口授权](https://help.aliyun.com/zh/direct-mail/api-dm-2015-11-23-singlesendmail)
5. 核对免费额度、账号日额度与费用告警。应用现有邮件冷却和 IP 限额不能代替云账号账单限制，其他应用共用同一账号也会消耗免费量。

最小 RAM 权限示例（该动作官方不支持更细的资源级授权）：

```json
{
  "Version": "1",
  "Statement": [
    { "Effect": "Allow", "Action": "dm:SingleSendMail", "Resource": "*" }
  ]
}
```

## 私有配置

保留原 `SECRET_KEY`、数据库目录与已有配置文件，不用新建数据库或重新生成站点密钥。以下为**键名和示例占位符**，不是可用凭据：

```text
LISS_EMAIL_ACCOUNTS=1
LISS_MAIL_PROVIDER=aliyun
LISS_MAIL_SENDER_EMAIL=accounts@your-verified-mail-domain.example
LISS_MAIL_SENDER_NAME=LISS
LISS_PUBLIC_BASE_URL=https://liss.pythonanywhere.com
LISS_ALIYUN_REGION=cn-hangzhou
LISS_ALIYUN_ACCESS_KEY_ID=<private RAM AccessKey ID>
LISS_ALIYUN_ACCESS_KEY_SECRET=<private RAM AccessKey Secret>
LISS_ALIYUN_SENDER_VERIFIED=0
```

最后一个开关默认关闭，只有控制台域名和发信地址验证完毕后才设为 `1`。它是操作者的设置确认，无法替代服务商的实时验证；撤销域名或密钥后，服务商拒绝发送时仍返回失败。缺少任一凭据、发件地址、HTTPS 网站地址或有效地域时不会启用邮件功能。

地域必须与控制台发信地址一致。代码限定官方 HTTPS 地址，不能通过配置改为任意服务器。[官方地域表](https://help.aliyun.com/zh/direct-mail/api-endpoints)

| 地域 | `LISS_ALIYUN_REGION` | API 主机 |
|---|---|---|
| 中国杭州 | `cn-hangzhou` | `dm.aliyuncs.com` |
| 新加坡 | `ap-southeast-1` | `dm.ap-southeast-1.aliyuncs.com` |
| 美国（原悉尼标识） | `ap-southeast-2` | `dm.ap-southeast-2.aliyuncs.com` |
| 美国新区域 | `us-east-1` | `dm.us-east-1.aliyuncs.com` |
| 德国法兰克福 | `eu-central-1` | `dm.eu-central-1.aliyuncs.com` |

PythonAnywhere 免费账号通过代理和域名 allowlist 对外访问。当前官方列表包含 `.aliyuncs.com`，但上线仍需从该账号验证 HTTPS 连通性；若网络策略后来变动，按平台申请流程处理，不关闭 TLS 验证或绕过限制。[官方 allowlist](https://www.pythonanywhere.com/whitelist/)、[网络说明](https://help.pythonanywhere.com/pages/403ForbiddenError)

## 实现与验收边界

`app/aliyun_mail.py` 使用 Python 标准库实现官方 RPC v1 签名，调用 `SingleSendMail` 的 `2015-11-23` API。密钥不进入请求网址；邮件正文与签名放在 HTTPS POST 中。限制单收件人、80 KiB 请求和 64 KiB 响应；不跟随重定向、不自动重试、不输出凭据或邮件链接，避免因超时重发重复邮件。[签名规范与公开测试向量](https://www.alibabacloud.com/help/en/direct-mail/signature)

仅收到正常 2xx 且含 `EnvId`、`RequestId` 的响应才标记“服务商已接受”；2xx 业务错误、拒绝、超时或异常格式均返回失败。**服务商接受不代表已经送达收件箱**。现有 Brevo、Resend 与仅限测试的 outbox 继续保留。

邮箱始终可选。用户可以不绑定邮箱注册、之后绑定或重新绑定；原邮箱在新邮箱确认前保留。密码找回仅适用于已经确认绑定邮箱的账户。

```powershell
.venv-public313\Scripts\python -m unittest discover -s tests -p test_aliyun_mail.py -v
.venv-public313\Scripts\python -m unittest discover -s tests -p test_email_accounts.py -v
```

隔离测试覆盖官方固定签名向量、中文和特殊字符、5 个地域、缺配置禁用、2xx 业务错误、无自动重试、超时、重定向、限长及 Flask 接入；不使用真实凭据或网络。开通后仍须经账号所有者授权，用其本人控制的邮箱实际验证确认、换绑与找回闭环，并查看服务商投递结果。该步骤完成前不能宣称真实邮件已上线。
