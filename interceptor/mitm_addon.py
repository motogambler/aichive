from mitmproxy import http
from mitmproxy import ctx
import os
import traceback
from interceptor.db import DB
import threading
from interceptor import compaction_worker as _compaction_worker
import yaml
from interceptor import metrics as _metrics
import time as _time
import gzip
import random
from interceptor.provider import choose_best_encoding, parse_accept_encoding_header, set_provider_support, get_provider_support
from interceptor.metrics import record_encoding_fallback
from interceptor.redact import redact_text
from interceptor.code_compress import compress_code


CONFIG_PATH = os.environ.get('INTERCEPTOR_CONFIG', './interceptor/config.yaml')


def _load_config():
    default = {
        'include_patterns': [],
        'exclude_patterns': [],
        'max_body_bytes': 1024 * 1024,
    }
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            if yaml is not None:
                cfg = yaml.safe_load(f) or {}
            else:
                # basic fallback parser: read simple key: value pairs for top-level only
                cfg = {}
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if ':' in line:
                        k, v = line.split(':', 1)
                        cfg[k.strip()] = v.strip()
            # coerce types
            if 'max_body_bytes' in cfg:
                try:
                    cfg['max_body_bytes'] = int(cfg['max_body_bytes'])
                except Exception:
                    # leave as-is if cannot convert
                    pass
            # ensure include/exclude patterns are lists
            for key in ('include_patterns', 'exclude_patterns'):
                if key in cfg and isinstance(cfg[key], str):
                    cfg[key] = [cfg[key]]
            default.update(cfg)
    except FileNotFoundError:
        pass
    return default


cfg = _load_config()
db = DB()


def _should_store(url: str) -> bool:
    # simple include/exclude logic
    inc = cfg.get('include_patterns') or []
    exc = cfg.get('exclude_patterns') or []
    if inc:
        if not any(p in url for p in inc):
            return False
    if any(p in url for p in exc):
        return False
    return True


class Interceptor:
    def load(self, loader) -> None:
        """mitmproxy hook called when the addon is loaded.

        Start a periodic, non-destructive compaction worker if configured via
        the `INTERCEPTOR_COMPACTION_INTERVAL` env var (seconds) or
        `compaction_interval` in the config file.
        """
        try:
            interval = int(os.environ.get('INTERCEPTOR_COMPACTION_INTERVAL', cfg.get('compaction_interval', 0) or 0))
        except Exception:
            interval = 0
        if not interval:
            ctx.log.info('Periodic compaction disabled (interval=0)')
        # start metrics server if requested
        try:
            if os.environ.get('INTERCEPTOR_METRICS_ENABLED') and _metrics is not None:
                port = int(os.environ.get('INTERCEPTOR_METRICS_PORT', '8000'))
                ok = _metrics.start_metrics_server(port=port)
                ctx.log.info(f'Metrics server enabled: {ok} (port={port})')
                _metrics.init_metrics()
        except Exception:
            ctx.log.warn('Failed to start metrics server: %s' % traceback.format_exc())
            return
        self._compaction_stop = threading.Event()
        # lock/flag to prevent concurrent compaction runs
        self._compaction_lock = threading.Lock()
        self._compaction_running = False
        # allow opt-in on-submission behavior via env or config
        compaction_on_submit = bool(os.environ.get('INTERCEPTOR_COMPACTION_ON_SUBMISSION')) or (cfg.get('compaction_mode') == 'on_submission')
        # debounce settings for on-submission mode
        self._compaction_debounce_interval = int(os.environ.get('INTERCEPTOR_COMPACTION_DEBOUNCE_SECONDS', cfg.get('compaction_debounce_seconds', 5) or 5))
        self._compaction_debounce_jitter = float(os.environ.get('INTERCEPTOR_COMPACTION_DEBOUNCE_JITTER', cfg.get('compaction_debounce_jitter', 2) or 2))
        self._compaction_debounce_timer = None
        if interval and not compaction_on_submit:
            def _loop():
                ctx.log.info(f'Starting periodic compaction loop every {interval}s')
                while not self._compaction_stop.wait(interval):
                    if _compaction_worker is None:
                        ctx.log.info('Compaction worker not available; skipping periodic run')
                        continue
                    try:
                        _t0 = _time.time()
                        _res = _compaction_worker.run_once(threshold=0.95, apply_prune=False)
                        ctx.log.info('Periodic compaction run completed')
                        try:
                            if _metrics is not None:
                                _metrics.record_run(start_ts=_t0)
                        except Exception:
                            pass
                    except Exception:
                        ctx.log.warn('Periodic compaction run failed: %s' % traceback.format_exc())

            t = threading.Thread(target=_loop, daemon=True)
            t.start()
            self._compaction_thread = t
            ctx.log.info('Periodic compaction worker started')
        else:
            self._compaction_thread = None
            if compaction_on_submit:
                ctx.log.info('Compaction will run on submission (no periodic loop)')
    def request(self, flow: http.HTTPFlow) -> None:
        try:
            url = flow.request.pretty_url
            if not _should_store(url):
                return

            content = flow.request.content or b""
            if cfg.get('max_body_bytes') and len(content) > cfg.get('max_body_bytes'):
                content = content[: cfg.get('max_body_bytes')]

            metadata = {"host": getattr(flow.request, 'host', None), "path": getattr(flow.request, 'path', None)}

            # redact PII from stored content if enabled
            try:
                try:
                    text = content.decode('utf-8')
                    text = redact_text(text)
                    store_content = text.encode('utf-8')
                except Exception:
                    store_content = content
            except Exception:
                store_content = content

            # store original full request content (redacted)
            msg_id = db.store_message("outbound", url, flow.request.method, dict(flow.request.headers), store_content, metadata)

            # Wire compression / restructuring options
            try:
                wire_cfg = cfg.get('compression', {}) or {}
                wire = wire_cfg.get('wire', {}) if isinstance(wire_cfg.get('wire', {}), dict) else {}
            except Exception:
                wire = {}

            strategy = wire.get('strategy') if wire.get('enabled') else None

            if strategy == 'restructure':
                # attempt to create a UTF-8 summary; skip binary bodies
                try:
                    text = content.decode('utf-8', errors='replace')
                except Exception:
                    text = None
                if text:
                    try:
                        wire_cfg = cfg.get('compression', {}) or {}
                        code_strategy = wire_cfg.get('code', 'ast')
                    except Exception:
                        code_strategy = 'ast'
                    is_code = ('\ndef ' in text) or text.lstrip().startswith('def ') or 'class ' in text or '{' in text
                    if is_code and code_strategy == 'ast':
                        try:
                            compact = compress_code(text, lang='python')
                            summary = compact
                        except Exception:
                            summary = text[:1024]
                    else:
                        head = text[:1024]
                        tail = text[-1024:] if len(text) > 2048 else ''
                        summary = head
                        if tail:
                            summary += "\n\n...\n\n" + tail
                    summary += f"\n\n[-- full content stored locally as message_id={msg_id} --]"
                    flow.request.content = summary.encode('utf-8')
                    flow.request.headers['X-LocAi-Ref'] = str(msg_id)
            elif strategy == 'content-encoding':
                try:
                    text = flow.request.content or b""
                    if isinstance(text, str):
                        text = text.encode('utf-8')
                    gz = gzip.compress(text)
                    accept = parse_accept_encoding_header(dict(flow.request.headers))
                    enc = choose_best_encoding(['gzip'], accept, preferred=['gzip'])
                    if enc == 'gzip':
                        flow.request.content = gz
                        flow.request.headers['Content-Encoding'] = 'gzip'
                        flow.request.headers['X-LocAi-Ref'] = str(msg_id)
                except Exception:
                    pass
            # schedule on-submission compaction (debounced) if configured
            try:
                if hasattr(self, '_compaction_thread') and self._compaction_thread is None:
                    self._schedule_compaction_debounced()
            except Exception:
                pass
        except Exception:
            ctx.log.warn("Failed to store request: %s" % traceback.format_exc())

    def response(self, flow: http.HTTPFlow) -> None:
        try:
            url = flow.request.pretty_url
            if not _should_store(url):
                return
            content = flow.response.content or b""
            if cfg.get('max_body_bytes') and len(content) > cfg.get('max_body_bytes'):
                content = content[: cfg.get('max_body_bytes')]
            metadata = {"status_code": flow.response.status_code, "url": url}
            # detect provider rejection of compressed requests and record capability
            try:
                req_enc = flow.request.headers.get('Content-Encoding')
                host = getattr(flow.request, 'host', None)
                if req_enc and flow.response.status_code >= 400:
                    try:
                        record_encoding_fallback(1)
                    except Exception:
                        pass
                    try:
                        if host:
                            set_provider_support(host, False, ttl=3600)
                    except Exception:
                        pass
            except Exception:
                pass
            # redact inbound content before storing
            try:
                try:
                    rtext = content.decode('utf-8')
                    rtext = redact_text(rtext)
                    store_resp = rtext.encode('utf-8')
                except Exception:
                    store_resp = content
            except Exception:
                store_resp = content
            db.store_message("inbound", url, flow.request.method, dict(flow.response.headers), store_resp, metadata)
            # schedule on-submission compaction (debounced) if configured
            try:
                if hasattr(self, '_compaction_thread') and self._compaction_thread is None:
                    self._schedule_compaction_debounced()
            except Exception:
                pass
        except Exception:
            ctx.log.warn("Failed to store response: %s" % traceback.format_exc())

    def done(self):
        """mitmproxy lifecycle hook called when the proxy is shutting down.

        We trigger a best-effort, non-destructive compaction run in the
        background so captured messages are compacted after a session.
        """
        try:
            if _compaction_worker is None:
                ctx.log.info('Compaction worker not available; skipping background run')
                return

            def _run():
                try:
                    # run a non-destructive compaction pass
                    _compaction_worker.run_once(threshold=0.95, apply_prune=False)
                    ctx.log.info('Background compaction run completed')
                except Exception:
                    ctx.log.warn('Background compaction run failed: %s' % traceback.format_exc())

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            ctx.log.info('Started background compaction run')

            # stop periodic thread if running
            try:
                if hasattr(self, '_compaction_stop'):
                    self._compaction_stop.set()
                    ctx.log.info('Signalled periodic compaction thread to stop')
            except Exception:
                ctx.log.warn('Failed to signal periodic compaction thread: %s' % traceback.format_exc())
        except Exception:
            ctx.log.warn('Failed to start background compaction: %s' % traceback.format_exc())

    def _trigger_compaction_once(self):
        """Run a single compaction pass if one is not already running."""
        if _compaction_worker is None:
            return
        # avoid concurrent compaction runs
        if getattr(self, '_compaction_lock', None) is None:
            self._compaction_lock = threading.Lock()
        if not self._compaction_lock.acquire(blocking=False):
            return
        try:
            self._compaction_running = True
            try:
                _compaction_worker.run_once(threshold=0.95, apply_prune=False)
                try:
                    if _metrics is not None:
                        _metrics.record_run(start_ts=_time.time())
                except Exception:
                    pass
                ctx.log.info('On-submission compaction run completed')
            except Exception:
                ctx.log.warn('On-submission compaction run failed: %s' % traceback.format_exc())
        finally:
            self._compaction_running = False
            try:
                self._compaction_lock.release()
            except Exception:
                pass

    def _schedule_compaction_debounced(self):
        """Schedule a debounced compaction run with optional jitter."""
        # cancel existing timer
        try:
            t = getattr(self, '_compaction_debounce_timer', None)
            if t is not None:
                try:
                    t.cancel()
                except Exception:
                    pass
        except Exception:
            pass

        wait = max(0, float(getattr(self, '_compaction_debounce_interval', 5)))
        jitter = float(getattr(self, '_compaction_debounce_jitter', 0))
        if jitter and jitter > 0:
            wait = wait + random.uniform(0, jitter)

        try:
            timer = threading.Timer(wait, self._trigger_compaction_once)
            timer.daemon = True
            timer.start()
            self._compaction_debounce_timer = timer
            ctx.log.info(f'Scheduled compaction in {wait:.2f}s (debounced)')
        except Exception:
            ctx.log.warn('Failed to schedule debounced compaction: %s' % traceback.format_exc())


addons = [Interceptor()]
