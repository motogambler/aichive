"""Production-ready MCP receiver (FastAPI).

This module provides a small FastAPI app exposing `/ingest` that accepts
JSON or JSON-lines, validates an API token when configured via
`INTERCEPTOR_MCP_TOKEN`, and stores received entries into the local DB.
"""
import os
import json
from typing import List


def create_app(db=None):
    try:
        from fastapi import FastAPI, Header, HTTPException, Request
    except Exception:  # fastapi not installed
        raise RuntimeError('fastapi is required to run the MCP app')

    app = FastAPI(title='loc-ai-storage MCP')

    TOKEN = os.environ.get('INTERCEPTOR_MCP_TOKEN')
    db = db or __import__('interceptor.db', fromlist=['DB']).DB()

    @app.post('/ingest')
    async def ingest(request: Request, authorization: str = Header(None)):
        if TOKEN:
            if not authorization or not authorization.startswith('Bearer '):
                raise HTTPException(status_code=401, detail='Missing Bearer token')
            sent = authorization.split(' ', 1)[1]
            if sent != TOKEN:
                raise HTTPException(status_code=403, detail='Invalid token')

        text = await request.body()
        if not text:
            raise HTTPException(status_code=400, detail='Empty payload')
        s = text.decode('utf-8', errors='replace')
        entries = []
        # try JSONL
        for line in s.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                entries = []
                break
        if not entries:
            # try single JSON
            try:
                j = json.loads(s)
                if isinstance(j, list):
                    entries = j
                else:
                    entries = [j]
            except Exception:
                raise HTTPException(status_code=400, detail='Invalid JSON payload')

        for obj in entries:
            try:
                mid = db.store_message('external', obj.get('url', ''), obj.get('method', 'POST'), obj.get('headers', {}), (obj.get('content') or '').encode('utf-8'), obj.get('metadata', {}))
            except Exception:
                # best-effort; continue
                continue
        return {'received': len(entries)}

    return app


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    app = create_app()
    try:
        import uvicorn
    except Exception:
        raise RuntimeError('uvicorn is required to run the MCP server')
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
