import os
from interceptor.db import DB
from dotenv import load_dotenv
import yaml

load_dotenv()

CONFIG_PATH = os.environ.get('INTERCEPTOR_CONFIG', './interceptor/config.yaml')


def load_cfg():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def run_purge(days: int = None):
    cfg = load_cfg()
    days = days if days is not None else cfg.get('retention_days', 90)
    db = DB()
    deleted = db.purge_older_than(days)
    print(f'Purged {deleted} messages older than {days} days')


if __name__ == '__main__':
    run_purge()
