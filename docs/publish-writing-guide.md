# 显式发布写作教学

正文来源为 [writing-guide.md](writing-guide.md)，插图是本站自托管的 [writing-guide.svg](../app/static/writing-guide.svg)。文章明确以站点作者 Liss 的身份发布，图片仅为写作流程示意，不冒充界面截图。正文不宣称邮件服务已启用。

更新源码后，使用与 WSGI 相同的私有配置和持久数据库，先检查：

```bash
python deploy/manage_public.py publish-writing-guide --dry-run
```

输出 `would-create` 后再执行实际发布：

```bash
python deploy/manage_public.py publish-writing-guide
```

命令仅支持已准备好的独立 `public-demo.sqlite`。它核对既有 `demo` 账号同时具有 `system` 身份、既有站点邮箱和已有文章，身份不符就停止；不会创建访客或把读者改成站点作者。首次发布前创建 `before-writing-guide-*.sqlite` 一致性备份，然后在一个事务中新增文章和 `site_guide_publications` 发布记录。

固定发布键为 `writing-guide-v1`。再次执行返回 `already-published` 和原文章 ID，不新增、不替换正文，不改时间、回复或任何其他文章。站点作者以后手动修改该教学，也不会被命令重写；如果原文已删除或作者发生变化，命令停止并要求维护者核对，而不是擅自重建。`--dry-run` 不创建表、不创建备份、不改写库。

普通请求与 `prepare-public-demo` 不调用此发布逻辑。修改 Markdown 文件后也不会自动重发或覆盖旧教学；后续内容修订应由站点维护者另行明确执行。

读者固定入口为 `/writing-guide/`（`community.writing_guide`），已发布后以 302 跳转到真实文章详情；未发布或作者身份异常则返回 404。`app.writing_guide.get_published_writing_guide()` 只读查询发布记录，缺表时返回 `None`，不会创建表或文章。
