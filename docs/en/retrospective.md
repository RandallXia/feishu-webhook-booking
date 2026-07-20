# Retrospective

## Language

- English: [Retrospective](retrospective.md)
- Chinese: [迁移复盘](../retrospective.md)

## Timeline

1. The old Feishu webhook / bot entry stopped working
2. A local NAS-hosted FastAPI bridge was built first
3. Creating new records first led to Feishu `403 / 91403 Forbidden`
4. The service was corrected to update a fixed `FEISHU_RECORD_ID`
5. Public ingress, DSM reverse proxy, and 443 blocking had to be solved
6. 8443, `plain http request was sent to https port`, DNS / AAAA / reverse-proxy protocol mismatches all appeared
7. The Shortcut payload moved from old `text` / `time` fields to `original_text` / `source` / `raw_ocr`
8. Windows PowerShell Chinese body encoding caused trouble
9. Annual book switching had to rely on external config, not rebuilding images
10. The final stable path was confirmed and kept running

## What we learned

- keep the business flow simple
- keep secrets external
- switch annual books by changing config, not images
- let Shortcut stay at the edge, not carry Feishu internals
