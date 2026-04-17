"""MCP / JSONL export stub.

Provides a minimal `export_to_jsonl` function and a placeholder
`export_to_mcp` that will POST JSON-lines to a configured MCP endpoint
if `requests` is available; otherwise it writes a JSONL file locally.
"""
import json
import os
from typing import Optional
from interceptor.db import DB


def export_to_jsonl(path: str = 'export.jsonl') -> str:
    db = DB()
    rows = db.get_all_messages()
    with open(path, 'w', encoding='utf-8') as f:
        for r in rows:
            mid = r[0]
            try:
                content = db.get_message_content(mid).decode('utf-8', errors='replace')
            except Exception:
                content = ''
            obj = {
                'id': mid,
                'direction': r[1],
                'url': r[2],
                'method': r[3],
                'ts': r[4],
                'headers': json.loads(r[5] or '{}'),
                'content': content,
                'metadata': json.loads(r[8] or '{}') if len(r) > 8 else {},
            }
            f.write(json.dumps(obj) + '\n')
    return os.path.abspath(path)


def export_to_mcp(endpoint: str, api_key: Optional[str] = None, batch_size: int = 50, max_retries: int = 3) -> bool:
    """POST exported messages to an MCP endpoint in batches with retries.

    Falls back to writing a local JSONL file when `requests` is not present.
    Returns True on success, False on failure (or when falling back).
    """
    try:
        import requests
    except Exception:
        export_to_jsonl('export.jsonl')
        return False

    db = DB()
    rows = db.get_all_messages()
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    # helper to POST a batch with simple retry/backoff
    def post_batch(batch):
        import time
        payload = '\n'.join(json.dumps(obj) for obj in batch)
        url = endpoint
        attempt = 0
        backoff = 0.5
        while attempt <= max_retries:
            try:
                resp = requests.post(url, headers=headers, data=payload, timeout=10)
                if 200 <= resp.status_code < 300:
                    return True
                # treat 4xx as unrecoverable
                if 400 <= resp.status_code < 500:
                    return False
            except Exception:
                pass
            attempt += 1
            time.sleep(backoff)
            backoff *= 2
        return False

    batch = []
    for r in rows:
        mid = r[0]
        try:
            content = db.get_message_content(mid).decode('utf-8', errors='replace')
        except Exception:
            content = ''
        obj = {
            'id': mid,
            'direction': r[1],
            'url': r[2],
            'method': r[3],
            'ts': r[4],
            'headers': json.loads(r[5] or '{}'),
            'content': content,
            'metadata': json.loads(r[8] or '{}') if len(r) > 8 else {},
        }
        batch.append(obj)
        if len(batch) >= batch_size:
            ok = post_batch(batch)
            if not ok:
                return False
            batch = []
    if batch:
        return post_batch(batch)
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mcp', type=str, default=None, help='MCP endpoint to POST to')
    parser.add_argument('--out', type=str, default='export.jsonl', help='Local JSONL output path')
    parser.add_argument('--api-key', type=str, default=None, help='Optional API key for MCP')
    args = parser.parse_args()
    if args.mcp:
        ok = export_to_mcp(args.mcp, api_key=args.api_key)
        if ok:
            print('Exported to MCP')
        else:
            print('Failed to export to MCP; wrote JSONL fallback')
            print(export_to_jsonl(args.out))
    else:
        print(export_to_jsonl(args.out))


if __name__ == '__main__':
    main()
