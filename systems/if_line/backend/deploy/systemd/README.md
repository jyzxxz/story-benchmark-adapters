# systemd deployment

These units assume the current server path
`/home/workspace/fengbohan/if_line/backend`. Install and enable them as root,
but do not start them until the migration, verification, and cutover gate have
completed:

```bash
install -m 0644 deploy/systemd/if-line-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable if-line-api if-line-worker if-line-beat
```

All three units load `backend/.env` through `EnvironmentFile`. The worker also
sources the same shell-compatible file because systemd does not expand
`${OTHER_VARIABLE}` references inside an EnvironmentFile. Keep the file mode at
`0600`; do not copy credentials into unit metadata.

Follow `docs/operations/story-path-cutover-runbook.md` from the repository root
for the required service order. The API and production frontend share port
`60002`; worker and beat open no inbound port.
