"""Minimal MCP receiver for local integration tests.

Starts a simple HTTP server with a `/ingest` endpoint that accepts
JSON-lines via POST and stores received entries in-memory for tests.
"""
import threading
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List


class _Handler(BaseHTTPRequestHandler):
    server_version = 'loc-ai-mcp/0.1'

    def do_POST(self):
        if self.path != '/ingest':
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get('Content-Length', '0'))
        data = self.rfile.read(length)
        # accept both JSONL or single JSON
        text = data.decode('utf-8', errors='replace')
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                try:
                    entries.append(json.loads(text))
                    break
                except Exception:
                    pass
        self.server._received.extend(entries)
        self.send_response(200)
        self.end_headers()


class MCPServer:
    def __init__(self, host='127.0.0.1', port=0):
        self._host = host
        self._port = port
        self._httpd = None
        self._thread = None

    def start(self):
        self._httpd = HTTPServer((self._host, self._port), _Handler)
        self._httpd._received: List[dict] = []
        addr = self._httpd.server_address
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return addr

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=1)

    def url(self):
        host, port = self._httpd.server_address
        return f'http://{host}:{port}/ingest'

    def received(self):
        return list(self._httpd._received)


def main():
    import argparse, time
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=0)
    args = p.parse_args()
    srv = MCPServer(port=args.port)
    addr = srv.start()
    print('MCP server started on', addr)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == '__main__':
    main()
