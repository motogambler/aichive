"""Provider capability / Content-Encoding negotiation helpers.

Small utilities used by the proxy to decide whether a provider supports
compressed responses and to choose a reasonable request-side encoding
when restructuring prompts or applying Content-Encoding.
"""
from typing import Dict, List, Optional
import time
import threading

"""
_CAPABILITY_CACHE maps host -> dict with keys:
    - supports: bool
    - expires: float (expiry timestamp)
    - next_probe: float (timestamp when probe should next run)
    - backoff: float (current backoff seconds)
"""
_CAPABILITY_CACHE: Dict[str, dict] = {}
_PROBE_THREAD = None
_PROBE_STOP = False

# Config knobs (can be overridden via env vars)
_BASE_PROBE_INTERVAL = float(__import__('os').environ.get('PROVIDER_PROBE_BASE_INTERVAL', 60.0))
_MAX_PROBE_BACKOFF = float(__import__('os').environ.get('PROVIDER_PROBE_MAX_BACKOFF', 3600.0))
_DEFAULT_TTL = int(__import__('os').environ.get('PROVIDER_PROBE_TTL', 3600))


def _split_encodings(header_val: str) -> List[str]:
    # Accept header like: gzip, deflate, br
    return [p.strip().lower() for p in header_val.split(',') if p.strip()]


def get_response_content_encodings(response_headers: Dict[str, str]) -> List[str]:
    """Return list of encodings present in a provider response's
    `Content-Encoding` header (may be comma-separated)."""
    if not response_headers:
        return []
    v = None
    # keys may be varying-case
    for k in ('content-encoding', 'Content-Encoding'):
        if k in response_headers:
            v = response_headers.get(k)
            break
    if not v:
        return []
    try:
        return _split_encodings(v)
    except Exception:
        return []


def parse_accept_encoding_header(request_headers: Dict[str, str]) -> List[str]:
    """Parse `Accept-Encoding` from request headers if present."""
    if not request_headers:
        return []
    v = None
    for k in ('accept-encoding', 'Accept-Encoding'):
        if k in request_headers:
            v = request_headers.get(k)
            break
    if not v:
        return []
    return _split_encodings(v)


def choose_best_encoding(server_enc: List[str], client_accept: List[str], preferred: Optional[List[str]] = None) -> Optional[str]:
    """Return the best matching encoding given server-supported encodings
    and client `Accept-Encoding` tokens. Preference order can be provided.
    Returns None if no suitable encoding is found.
    """
    if preferred is None:
        preferred = ['br', 'gzip', 'deflate', 'lz4']
    server_set = set([s.lower() for s in server_enc or []])
    accept_set = set([s.lower() for s in client_accept or []])
    # intersection in preferred order
    for p in preferred:
        if p in server_set and (not accept_set or p in accept_set):
            return p
    # fallback: any server encoding
    if server_set:
        return next(iter(server_set))
    return None


def provider_supports_compressed_responses(response_headers: Dict[str, str]) -> bool:
    return bool(get_response_content_encodings(response_headers))


def set_provider_support(host: str, supports: bool, ttl: int = 3600):
    """Record whether a host supports compressed responses. TTL in seconds."""
    now = time.time()
    expires = now + ttl
    entry = _CAPABILITY_CACHE.get(host, {})
    entry.update({'supports': bool(supports), 'expires': expires})
    # reset backoff when provider indicates support
    if supports:
        entry['backoff'] = _BASE_PROBE_INTERVAL
        entry['next_probe'] = now + _BASE_PROBE_INTERVAL
    else:
        # schedule next probe after a default base interval (subject to backoff growth if probe fails)
        entry.setdefault('backoff', _BASE_PROBE_INTERVAL)
        entry['next_probe'] = now + entry['backoff']
    _CAPABILITY_CACHE[host] = entry


def get_provider_support(host: str) -> Optional[bool]:
    """Return cached provider support if available and not expired, else None."""
    if not host:
        return None
    val = _CAPABILITY_CACHE.get(host)
    if not val:
        return None
    supports = val.get('supports')
    expires = val.get('expires', 0)
    if time.time() > expires:
        # expired
        try:
            del _CAPABILITY_CACHE[host]
        except Exception:
            pass
        return None
    return bool(supports)


def start_probe_loop(probe_fn, interval: float = None, ttl_check: float = 0.0):
    """Start a background thread that probes hosts in the capability cache.

    - `probe_fn(host) -> bool` should return True if host supports encoding.
    - `interval` is the base scan interval; defaults to env/config `_BASE_PROBE_INTERVAL`.
    - `ttl_check` when >0 causes only entries with expired TTL to be probed; when 0 all cached hosts are considered for per-host scheduling.
    """
    global _PROBE_THREAD, _PROBE_STOP, _BASE_PROBE_INTERVAL
    if _PROBE_THREAD is not None:
        return
    _PROBE_STOP = False
    base_interval = float(interval) if interval is not None else _BASE_PROBE_INTERVAL

    def _loop():
        while not _PROBE_STOP:
            now = time.time()
            hosts = list(_CAPABILITY_CACHE.keys())
            for h in hosts:
                try:
                    entry = _CAPABILITY_CACHE.get(h)
                    if not entry:
                        continue
                    expires = entry.get('expires', 0)
                    next_probe = entry.get('next_probe', 0)
                    backoff = entry.get('backoff', base_interval)
                    # ttl_check >0: only probe entries whose TTL expired
                    if ttl_check > 0 and expires > now:
                        continue
                    # if ttl_check == 0, probe immediately regardless of next_probe
                    if ttl_check != 0:
                        # skip until its next_probe time
                        if next_probe and next_probe > now:
                            continue
                    ok = probe_fn(h)
                    if ok:
                        # provider supports encoding: reset backoff and set ttl
                        set_provider_support(h, True, ttl=_DEFAULT_TTL)
                    else:
                        # probe failed: increase backoff (exponential) and schedule next probe
                        entry = _CAPABILITY_CACHE.get(h, {})
                        cur_backoff = float(entry.get('backoff', base_interval))
                        next_backoff = min(cur_backoff * 2 if cur_backoff else base_interval, _MAX_PROBE_BACKOFF)
                        entry['backoff'] = next_backoff
                        entry['next_probe'] = now + next_backoff
                        entry['supports'] = False
                        entry['expires'] = now + _DEFAULT_TTL
                        _CAPABILITY_CACHE[h] = entry
                except Exception:
                    pass
            time.sleep(base_interval)

    t = threading.Thread(target=_loop, daemon=True)
    _PROBE_THREAD = t
    t.start()


def stop_probe_loop():
    global _PROBE_STOP, _PROBE_THREAD
    _PROBE_STOP = True
    if _PROBE_THREAD is not None:
        _PROBE_THREAD.join(timeout=1)
    _PROBE_THREAD = None
