# 写作编辑器

2026-10-24：EasyMDE 2.21.0 + DOMPurify 3.4.17，资源与许可证固定版本、自托管在 app/static/vendor/，来源及 SHA256 见 editor-manifest.json；npm 包 SHA512 与注册表完整性值已核对。运行不需要 Node、Font Awesome、外部 CDN 或第三方图片上传服务。

- 保留普通 Markdown 原文，中文工具栏支持格式、外链图片、预览、桌面分屏和全屏；手机分屏按钮切换整页预览。
- 插图仅填写链接，不上传文件；本站教学图通过自有静态目录提供。未启用跨身份本地草稿恢复。
- 渐进增强：库不可用时原生 textarea 仍可提交。强制同步原表单，CSRF、后端配额与原有编辑权限不变。
- DOMPurify 清理即时预览；Python Bleach 单次清洗+LinkifyFilter负责发布内容，保留图片alt/title，代码中URL与实体不被重复处理。
- 访客10篇（文章+项目讨论合计），注册账号不限篇数；单篇长度、频率和数据库容量限制保留。说明位于写作框、注册/欢迎页及发文指南；页脚持续提供指南入口。

官方项目：[EasyMDE](https://github.com/Ionaru/easy-markdown-editor)、[DOMPurify](https://github.com/cure53/DOMPurify)。比较过 Vditor 和 Milkdown；当前 Flask 站点采用直接增强 textarea 的方案，避免引入前端应用构建和大量当前不支持的语法。
