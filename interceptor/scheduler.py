import time
import os
from datetime import datetime
import subprocess

# Simple scheduler: run ingest every 5 minutes, run retention once daily at 03:00
INGEST_INTERVAL = int(os.environ.get('SCHED_INGEST_INTERVAL', '300'))
RETENTION_HOUR = int(os.environ.get('SCHED_RETENTION_HOUR', '3'))
PROJECT_DIR = os.environ.get('PROJECT_DIR', '/app')


def run_cmd(cmd):
    try:
        subprocess.run(cmd, shell=True, check=False)
    except Exception:
        pass


def main():
    last_retention_day = None
    while True:
        # run ingest
        run_cmd(f'python {PROJECT_DIR}/interceptor/ingest_worker.py --once')
        # run tokenizer
        run_cmd(f'python {PROJECT_DIR}/interceptor/tokenize_worker.py --once')
        now = datetime.utcnow()
        if now.day != last_retention_day and now.hour == RETENTION_HOUR:
            run_cmd(f'python {PROJECT_DIR}/interceptor/retention.py')
            last_retention_day = now.day
        time.sleep(INGEST_INTERVAL)


if __name__ == '__main__':
    main()
