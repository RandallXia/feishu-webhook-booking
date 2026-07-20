# Windows deployment

## Language

- English: [Windows deployment](windows.md)
- Chinese: [Windows 部署](../../deployment/windows.md)

## How to run

- Python 3.12+
- `venv`
- `pip install -r requirements-dev.txt`
- `uvicorn app.main:app --host 0.0.0.0 --port 2398`

## Keep it running

Options:

- Task Scheduler
- persistent Remote Desktop machine
- simple startup script

## Notes

- be careful with UTF-8 when testing in PowerShell
- open the port in Windows Firewall
- prefer Cloudflare Tunnel or a controlled reverse proxy for public access

## Annual switching

Update external config and restart; do not bake secrets into bat / exe / image.
