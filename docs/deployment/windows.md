# Windows 部署 / Windows deployment

## 语言切换 / Language

- 中文（默认）: [本页](windows.md)
- English: [Windows](../../en/deployment/windows.md)

## 运行方式

- Python 3.12+
- `venv`
- `pip install -r requirements-dev.txt`
- `uvicorn app.main:app --host 0.0.0.0 --port 2398`

## 后台常驻

可以使用：

- 任务计划程序
- 远程桌面主机常驻
- 本地开机自启脚本

## 注意事项

- PowerShell 测试示例要注意 UTF-8
- Windows 防火墙要开放端口
- 公网访问优先用 Cloudflare Tunnel 或受控反向代理

## 年度切换

外部配置变更后重启即可，不要把敏感配置写进 bat / exe / 镜像。
