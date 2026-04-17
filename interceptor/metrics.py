"""Simple Prometheus metrics helper for compaction runs.

Start server with `start_metrics_server()` when INTERCEPTOR_METRICS_ENABLED is truthy.
Provide helpers to record compaction run counts/duration and created/pruned counters.
"""
import os
import threading
from prometheus_client import start_http_server, Counter, Histogram

_metrics = {}


def start_metrics_server(port: int = 8000):
    # start server in background thread
    t = threading.Thread(target=lambda: start_http_server(port), daemon=True)
    t.start()
    return True


def init_metrics():
    global _metrics
    if _metrics:
        return
    _metrics['compaction_runs_total'] = Counter('locai_compaction_runs_total', 'Total compaction runs')
    _metrics['compaction_created_total'] = Counter('locai_compaction_created_total', 'Total compaction entries created')
    _metrics['compaction_pruned_total'] = Counter('locai_compaction_pruned_total', 'Total compaction messages pruned')
    _metrics['compaction_duration_seconds'] = Histogram('locai_compaction_duration_seconds', 'Compaction duration in seconds')
    _metrics['encoding_fallbacks_total'] = Counter('locai_encoding_fallbacks_total', 'Total encoding fallbacks due to provider errors')


def record_run(start_ts, created=0, pruned=0):
    if not _metrics:
        return
    _metrics['compaction_runs_total'].inc()
    if created:
        _metrics['compaction_created_total'].inc(created)
    if pruned:
        _metrics['compaction_pruned_total'].inc(pruned)
    # observe duration
    try:
        _metrics['compaction_duration_seconds'].observe(max(0.0, (os.times()[4] - start_ts)))
    except Exception:
        pass


def record_encoding_fallback(count: int = 1):
    if not _metrics:
        return
    try:
        _metrics['encoding_fallbacks_total'].inc(count)
    except Exception:
        pass
