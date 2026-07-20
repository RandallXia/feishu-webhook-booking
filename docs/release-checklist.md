# 发布核对 / Release checklist

## 语言切换 / Language

- 中文（默认）: [本页](release-checklist.md)
- English: [Release checklist](en/release-checklist.md)

## 目标

在对外开源之前，确认仓库已经符合当前动态路由版本的发布标准。

## 必查项

- 所有真实 token、账号、域名都已替换为占位符
- 敏感截图已删除或已打码
- `LICENSE` 已补齐
- `.gitignore` 已补齐
- `.env.example` 和 `runtime/feishu-targets.toml.example` 与当前配置项一致
- README 能从首页直接理解项目定位、运行模式和文档入口
- 所有文档链接都可点击跳转
- 所有部署示例都与当前代码一致
- 至少跑通一条真实验证路径
- 年度账本切换演练已验证：修改外部配置并重启或 reload 后可切换账本，无需重建镜像

## 发布前建议

1. 再次核对 `app/main.py`、`app/config.py`、`app/target_registry.py` 的字段名与文档是否一致
2. 确认 README 中的中文 / 英文入口链接没有失效
3. 确认 `runtime/feishu-targets.toml.example` 里的示例值全部是占位符
4. 确认没有把真实运行时文件误提交到仓库

## 通过标准

当以上检查都通过后，这个仓库可以作为对外开源版本发布。
