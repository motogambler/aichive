# Running periodic ingest & retention

Systemd unit (example) to run the ingest worker continuously:

```
[Unit]
Description=Loc-AI-Storage Ingest Worker
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/loc-ai-storage
ExecStart=/usr/bin/python3 interceptor/ingest_worker.py
Restart=on-failure
User=youruser

[Install]
WantedBy=multi-user.target
```

Cron example to run the ingest worker every 5 minutes and retention daily at 03:00:

```cron
# every 5 minutes
*/5 * * * * cd /path/to/loc-ai-storage && /usr/bin/python3 interceptor/ingest_worker.py --once >> /var/log/loc-ai-ingest.log 2>&1
# daily retention
0 3 * * * cd /path/to/loc-ai-storage && /usr/bin/python3 interceptor/retention.py >> /var/log/loc-ai-retention.log 2>&1
```

Adjust paths and user permissions appropriately. For Docker, add a small sidecar or cron image to trigger `--once` runs.
