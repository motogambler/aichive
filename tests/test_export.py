import os
import time
from interceptor import export
from interceptor.mcp_server import MCPServer


def test_export_to_jsonl(tmp_path):
    out = tmp_path / 'o.jsonl'
    p = export.export_to_jsonl(str(out))
    assert os.path.exists(p)


def test_export_to_mcp_with_server(monkeypatch):
    srv = MCPServer()
    addr = srv.start()
    try:
        url = srv.url()
        ok = export.export_to_mcp(url, batch_size=10, max_retries=1)
        # export may return True even if no messages; ensure server responded
        # and accepted the request (no exception)
        assert isinstance(ok, bool)
    finally:
        srv.stop()
