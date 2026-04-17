#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
import subprocess
import shutil
import sys
import os
import signal
import time
import platform
import getpass

from interceptor.db import DB
DB().purge_older_than(30)  # delete messages >30 days


def cmd_list(args):
    db = DB()
    msgs = db.get_all_messages()
    out = []
    for m in msgs:
        out.append({
            "id": m[0],
            "direction": m[1],
            "url": m[2],
            "method": m[3],
            "ts": datetime.fromtimestamp(m[4]).isoformat(),
            "headers": json.loads(m[5]) if m[5] else {},
            "hash": m[7],
            "metadata": json.loads(m[8]) if m[8] else {},
        })
    print(json.dumps(out, indent=2))


def cmd_show(args):
    db = DB()
    msgs = db.get_all_messages()
    mid = int(args.id)
    row = None
    for m in msgs:
        if m[0] == mid:
            row = m
            break
    if not row:
        print(f"message id {mid} not found")
        return
    headers = json.loads(row[5]) if row[5] else {}
    print('--- meta ---')
    print('id:', row[0])
    print('direction:', row[1])
    print('url:', row[2])
    print('method:', row[3])
    print('ts:', datetime.fromtimestamp(row[4]).isoformat())
    print('headers:', json.dumps(headers, indent=2))
    print('hash:', row[7])
    print('metadata:', row[8])
    print('\n--- content (first 4096 bytes) ---')
    data = db.get_message_content(mid)
    try:
        text = data.decode('utf-8')
        print(text[:4096])
    except Exception:
        # binary fallback
        import base64

        print(base64.b64encode(data)[:4096])


def cmd_proxy(args):
    """Start mitmproxy/mitmdump with the interceptor addon script."""
    # prefer mitmdump (non-interactive) for CLI run
    use_mitmdump = bool(getattr(args, 'mitmdump', False))
    exe = shutil.which('mitmdump' if use_mitmdump else 'mitmproxy')
    if not exe:
        # try the other
        exe = shutil.which('mitmdump' if not use_mitmdump else 'mitmproxy')
        if not exe:
            print('mitmproxy/mitmdump not found in PATH. Install mitmproxy to use the proxy command.')
            return
    # build command
    port = int(getattr(args, 'port', 8080))
    host = getattr(args, 'host', '0.0.0.0')
    script = os.path.abspath('interceptor/mitm_addon.py')
    cmd = [exe, '-s', script, '-p', str(port), '--listen-host', host]
    print(f"Starting proxy: {' '.join(cmd)}")
    try:
        # ensure subprocess can import local package by setting PYTHONPATH
        env = os.environ.copy()
        cwd = os.getcwd()
        prev = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = cwd + (os.pathsep + prev if prev else '')
        # suppress startup warnings from mitmproxy/cryptography in the subprocess
        # set to 'ignore' to avoid noisy deprecation messages during startup
        prev_w = env.get('PYTHONWARNINGS')
        env['PYTHONWARNINGS'] = (prev_w + ',ignore') if prev_w else 'ignore'
        # forward signals and attach to terminal
        return_code = subprocess.call(cmd, env=env)
        if return_code != 0:
            print(f'Proxy exited with code {return_code}')
    except KeyboardInterrupt:
        print('Proxy interrupted by user')
    except Exception as e:
        print('Failed to start proxy:', e)


def _find_pids_by_port(port: int):
    """Return list of PIDs listening on TCP port."""
    pids = []
    try:
        import psutil
        for conn in psutil.net_connections(kind='inet'):
            laddr = conn.laddr
            if not laddr:
                continue
            if getattr(laddr, 'port', None) == port and conn.status == psutil.CONN_LISTEN:
                pid = conn.pid
                if pid and pid not in pids:
                    pids.append(pid)
        return pids
    except Exception:
        pass

    # fallback to system tools
    system = platform.system()
    if system == 'Windows':
        try:
            out = subprocess.check_output(['netstat', '-ano', '-p', 'tcp'], universal_newlines=True)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].startswith('  TCP') or parts[0].startswith('TCP'):
                    # Local Address column like 0.0.0.0:8080
                    local = parts[1]
                    if local.endswith(':' + str(port)):
                        pid = parts[-1]
                        try:
                            pids.append(int(pid))
                        except Exception:
                            pass
        except Exception:
            pass
    else:
        # try lsof
        try:
            out = subprocess.check_output(['lsof', '-nP', '-iTCP:{0}'.format(port), '-sTCP:LISTEN', '-t'], universal_newlines=True)
            for line in out.splitlines():
                try:
                    pids.append(int(line.strip()))
                except Exception:
                    pass
            return pids
        except Exception:
            pass
        # fallback to ss parsing
        try:
            out = subprocess.check_output(['ss', '-ltnp'], universal_newlines=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                if ':' + str(port) in line and 'LISTEN' in line:
                    # attempt to extract pid from 'pid=1234,' pattern
                    import re
                    m = re.search(r'pid=(\d+),', line)
                    if m:
                        try:
                            pids.append(int(m.group(1)))
                        except Exception:
                            pass
        except Exception:
            pass
    return list(dict.fromkeys(pids))


def _kill_pid(pid: int, timeout: float = 5.0) -> bool:
    """Attempt to gracefully terminate PID, then force kill if needed."""
    try:
        import psutil
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=timeout)
                return True
            except Exception:
                p.kill()
                p.wait(timeout=timeout)
                return True
        except psutil.NoSuchProcess:
            return True
        except Exception:
            pass
    except Exception:
        pass

    system = platform.system()
    try:
        if system == 'Windows':
            subprocess.check_call(['taskkill', '/PID', str(pid)])
            return True
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return True
            time.sleep(min(0.1, timeout))
            # check if still alive
            try:
                os.kill(pid, 0)
                # still alive -> force
                os.kill(pid, signal.SIGKILL)
            except OSError:
                return True
            return True
    except Exception:
        return False


def cmd_proxy_stop(args):
    """Stop mitmdump by PID or by port."""
    pid = getattr(args, 'pid', None)
    port = getattr(args, 'port', None)
    targets = []
    if pid:
        targets = [int(pid)]
    elif port:
        targets = _find_pids_by_port(int(port))
    else:
        print('Specify --pid or --port to stop proxy')
        return
    if not targets:
        print('No matching mitmdump processes found')
        return
    for p in targets:
        ok = _kill_pid(p)
        print(f'Killed PID {p}: {ok}')


def cmd_proxy_restart(args):
    """Stop existing mitmdump on port and start a new one with given options."""
    port = getattr(args, 'port', 8080)
    # stop existing
    pids = _find_pids_by_port(int(port))
    for p in pids:
        ok = _kill_pid(p)
        print(f'Stopped PID {p}: {ok}')
    # start new proxy in background
    use_mitmdump = bool(getattr(args, 'mitmdump', False))
    exe = shutil.which('mitmdump' if use_mitmdump else 'mitmproxy')
    if not exe:
        exe = shutil.which('mitmdump' if not use_mitmdump else 'mitmproxy')
        if not exe:
            print('mitmproxy/mitmdump not found in PATH. Install mitmproxy to use the proxy command.')
            return
    script = os.path.abspath('interceptor/mitm_addon.py')
    cmd = [exe, '-s', script, '-p', str(port), '--listen-host', getattr(args, 'host', '127.0.0.1')]
    env = os.environ.copy()
    prev = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = os.getcwd() + (os.pathsep + prev if prev else '')
    prev_w = env.get('PYTHONWARNINGS')
    env['PYTHONWARNINGS'] = (prev_w + ',ignore') if prev_w else 'ignore'
    try:
        p = subprocess.Popen(cmd, env=env)
        print(f'Started proxy PID {p.pid} on port {port}')
    except Exception as e:
        print('Failed to start proxy:', e)


def cmd_mitm_ca(args):
    """Locate mitmproxy CA files and print their paths."""
    home = os.environ.get('MITMPROXY_HOME') or os.path.expanduser('~/.mitmproxy')
    home = os.path.abspath(os.path.expanduser(home))
    candidates = [
        'mitmproxy-ca-cert.pem',
        'mitmproxy-ca.pem',
        'mitmproxy-ca-cert.p12',
        'mitmproxy-ca.p12',
    ]
    found = []
    for name in candidates:
        p = os.path.join(home, name)
        if os.path.exists(p):
            found.append(p)
    if args.all:
        print(home)
        for f in found:
            print(f)
        if not found:
            print('# no CA files found; start mitmproxy to generate them')
        return
    # print the most likely CA cert path
    if found:
        print(found[0])
    else:
        print(home)
        print('# no CA file found; run mitmproxy/mitmdump once or visit http://mitm.it from a proxied browser')


def cmd_proxy_export(args):
    """Export selected messages as JSON. By default exports the most recent `--limit` messages.

    Options:
      --ids comma-separated ids to export (overrides --limit)
      --limit number of recent messages to export
      --out path to write JSON (stdout if omitted)
    """
    from interceptor.db import DB
    import base64

    db = DB()
    ids = []
    if getattr(args, 'ids', None):
        try:
            ids = [int(x) for x in args.ids.split(',') if x.strip()]
        except Exception:
            print('Invalid --ids format (expected comma-separated integers)')
            return
    else:
        msgs = db.get_all_messages()
        limit = int(getattr(args, 'limit', 100) or 100)
        ids = [m[0] for m in msgs[:limit]]

    out = []
    for mid in ids:
        try:
            row = None
            # fetch message metadata via get_all_messages isn't ideal; read directly
            with __import__('sqlite3').connect(db.path) as conn:
                c = conn.cursor()
                c.execute('SELECT id, direction, url, method, ts, headers, compressed, hash, metadata FROM messages WHERE id=?', (mid,))
                r = c.fetchone()
            if not r:
                continue
            msg = {
                'id': r[0],
                'direction': r[1],
                'url': r[2],
                'method': r[3],
                'ts': datetime.fromtimestamp(r[4]).isoformat(),
                'headers': json.loads(r[5]) if r[5] else {},
                'hash': r[7],
                'metadata': json.loads(r[8]) if r[8] else {},
            }
            # rehydrate content (chunks or raw)
            try:
                chunks = db.get_message_chunks(mid)
                if chunks:
                    data = db.rehydrate_message(mid)
                else:
                    data = db.get_message_content(mid)
            except Exception:
                data = b''
            try:
                text = data.decode('utf-8')
                msg['content'] = text
                msg['content_b64'] = None
            except Exception:
                msg['content'] = None
                msg['content_b64'] = base64.b64encode(data).decode('ascii')
            out.append(msg)
        except Exception as e:
            print('Failed to export message', mid, e)
    payload = json.dumps(out, indent=2)
    if getattr(args, 'out', None):
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(payload)
        print('Exported', len(out), 'messages to', args.out)
    else:
        print(payload)


def cmd_proxy_stats(args):
    """Compute performance and savings metrics: compression, dedupe, tokens, embeddings, FAISS size."""
    from interceptor.db import DB
    import math

    db = DB()
    # messages
    msgs = db.get_all_messages()
    msg_count = len(msgs)
    total_raw = 0
    total_comp = 0
    sizes = []
    for m in msgs:
        mid = m[0]
        try:
            data = db.get_message_content(mid)
            total_raw += len(data)
            sizes.append(len(data))
        except Exception:
            pass
    with __import__('sqlite3').connect(db.path) as conn:
        c = conn.cursor()
        c.execute('SELECT COALESCE(SUM(LENGTH(compressed)),0) FROM messages')
        total_comp = c.fetchone()[0] or 0
        c.execute('SELECT COUNT(*), COALESCE(SUM(size),0), COALESCE(SUM(LENGTH(compressed)),0) FROM chunks')
        chunk_count, chunks_raw_sum, chunks_comp_sum = c.fetchone()
        c.execute('SELECT COUNT(*) FROM tokenized')
        tokenized_count = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM embeddings')
        embedding_count = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM compactions')
        compactions = c.fetchone()[0]

    # dedupe: total unique chunk raw bytes vs total raw
    unique_chunk_raw = chunks_raw_sum or 0
    total_saved_by_dedupe = max(0, total_raw - unique_chunk_raw)

    compression_ratio = (total_raw / total_comp) if total_comp and total_raw else (1.0 if total_raw else 0.0)
    avg_msg = (sum(sizes) / len(sizes)) if sizes else 0

    # faiss file size (best-effort)
    faiss_path = os.path.abspath('interceptor/faiss.index')
    faiss_size = os.path.getsize(faiss_path) if os.path.exists(faiss_path) else None

    out = {
        'messages': msg_count,
        'messages_raw_bytes': total_raw,
        'messages_compressed_bytes': total_comp,
        'compression_ratio': round(compression_ratio, 3) if isinstance(compression_ratio, float) else compression_ratio,
        'avg_message_size': int(avg_msg),
        'chunks': int(chunk_count or 0),
        'chunks_raw_bytes': int(chunks_raw_sum or 0),
        'chunks_compressed_bytes': int(chunks_comp_sum or 0),
        'dedupe_saved_bytes': int(total_saved_by_dedupe),
        'tokenized_messages': int(tokenized_count or 0),
        'embeddings': int(embedding_count or 0),
        'compactions': int(compactions or 0),
        'faiss_index_path': faiss_path if faiss_size is not None else None,
        'faiss_index_size_bytes': int(faiss_size) if faiss_size is not None else None,
    }

    if getattr(args, 'json', False):
        print(json.dumps(out, indent=2))
    else:
        print('Messages:', out['messages'])
        print('Raw bytes:', out['messages_raw_bytes'])
        print('Compressed bytes:', out['messages_compressed_bytes'])
        print('Compression ratio (raw/compressed):', out['compression_ratio'])
        print('Avg message size:', out['avg_message_size'])
        print('Chunks:', out['chunks'])
        print('Chunks raw bytes:', out['chunks_raw_bytes'])
        print('Chunks compressed bytes:', out['chunks_compressed_bytes'])
        print('Deduplication saved bytes:', out['dedupe_saved_bytes'])
        print('Tokenized messages:', out['tokenized_messages'])
        print('Embeddings:', out['embeddings'])
        print('Compactions:', out['compactions'])
        if out['faiss_index_size_bytes'] is not None:
            print('FAISS index:', out['faiss_index_path'], out['faiss_index_size_bytes'], 'bytes')


def cmd_proxy_ca_install(args):
    """Install mitmproxy CA into the user's trust store (best-effort).

    This asks for confirmation unless --yes is provided. For system-wide installs
    use --system (requires admin) but the default is a user-level install.
    """
    # locate CA
    home = os.environ.get('MITMPROXY_HOME') or os.path.expanduser('~/.mitmproxy')
    home = os.path.abspath(os.path.expanduser(home))
    candidates = [
        args.cert if getattr(args, 'cert', None) else None,
        os.path.join(home, 'mitmproxy-ca-cert.pem'),
        os.path.join(home, 'mitmproxy-ca.pem'),
        os.path.join(home, 'mitmproxy-ca-cert.p12'),
        os.path.join(home, 'mitmproxy-ca.p12'),
    ]
    ca = None
    for c in candidates:
        if not c:
            continue
        if os.path.exists(c):
            ca = c
            break
    if not ca:
        print('No mitmproxy CA found. Run mitmproxy/mitmdump once to generate CA files or pass --cert <path>')
        return

    system = platform.system()
    do_system = bool(getattr(args, 'system', False))
    if do_system:
        print('System-wide install requested. This may require administrative privileges.')
    print(f'Found CA: {ca}')
    if not getattr(args, 'yes', False):
        resp = input(f'Install this certificate to the {'system' if do_system else 'user'} trust store? [y/N]: ')
        if resp.lower() not in ('y', 'yes'):
            print('Aborted by user')
            return

    try:
        if system == 'Windows':
            if do_system:
                cmd = ['certutil', '-addstore', 'Root', ca]
            else:
                cmd = ['certutil', '-addstore', '-user', 'Root', ca]
            subprocess.check_call(cmd)
            print('Certificate installed (Windows). You may need to restart applications.')
            return

        if system == 'Darwin':
            # macOS: try user keychain first
            if do_system:
                cmd = ['sudo', 'security', 'add-trusted-cert', '-d', '-r', 'trustRoot', '-k', '/Library/Keychains/System.keychain', ca]
            else:
                # install to login keychain (user-level)
                login_kc = os.path.expanduser('~/Library/Keychains/login.keychain-db')
                cmd = ['security', 'add-trusted-cert', '-d', '-r', 'trustRoot', '-k', login_kc, ca]
            subprocess.check_call(cmd)
            print('Certificate installed (macOS). You may need to restart apps.')
            return

        # Linux / other Unix-like
        # Prefer certutil (NSS) for per-user Firefox/curl support
        certutil_path = shutil.which('certutil')
        if certutil_path:
            nssdb = os.path.expanduser('~/.pki/nssdb')
            os.makedirs(nssdb, exist_ok=True)
            cmd = [certutil_path, '-d', f'sql:{nssdb}', '-A', '-n', 'mitmproxy', '-t', 'C,', '-i', ca]
            # create DB if missing
            try:
                subprocess.check_call(cmd)
                print('Certificate added to NSS DB (Firefox, user-level).')
            except subprocess.CalledProcessError:
                print('Failed to add to NSS DB; try running certutil manually or install system CA.')
            # also suggest REQUESTS_CA_BUNDLE
            print('Note: For Python requests/curl, set REQUESTS_CA_BUNDLE or CURL_CA_BUNDLE to this file, or install system CA.')
            return

        # fallback: advise system install
        print('Could not perform a user-level install automatically on this platform.')
        print('To trust the certificate system-wide, run one of the following (Linux example):')
        print(f'sudo cp "{ca}" /usr/local/share/ca-certificates/mitmproxy-ca.crt && sudo update-ca-certificates')
        print('Or set REQUESTS_CA_BUNDLE to the CA PEM for Python requests:')
        print(f'export REQUESTS_CA_BUNDLE="{ca}"')
    except Exception as e:
        print('Failed to install certificate:', e)

def cmd_ingest(args):
    try:
        from interceptor.ingest_worker import run_once
    except Exception as e:
        print('ingest_worker not available:', e)
        return
    run_once(limit=args.limit)


def cmd_tokenize(args):
    try:
        from interceptor.tokenize_worker import run_once
    except Exception as e:
        print('tokenize_worker not available:', e)
        return
    run_once(limit=args.limit, encoding=args.encoding)


def cmd_migrate_chunks(args):
    from interceptor.db import DB
    from interceptor.chunker import chunk_bytes, chunk_hash

    db = DB()
    ids = [m[0] for m in db.get_all_messages()]
    processed = 0
    for mid in ids[: args.limit]:
        # skip if already has chunks
        existing = db.get_message_chunks(mid)
        if existing:
            continue
        data = db.get_message_content(mid)
        chunks = chunk_bytes(data, chunk_size=args.chunk_size)
        hashes = []
        for ch in chunks:
            h = chunk_hash(ch)
            db.store_chunk(h, ch)
            hashes.append(h)
        if hashes:
            db.link_message_chunks(mid, hashes)
            processed += 1
    print(f'Migrated {processed} messages into chunks')


def cmd_search(args):
    from interceptor.search import semantic_search
    res = semantic_search(args.query, k=args.k)
    import json

    print(json.dumps(res, indent=2))


def cmd_faiss_rebuild(args):
    from interceptor.faiss_index import FaissIndex
    from interceptor.db import DB
    from interceptor.embeddings import EMBED_DIM

    db = DB()
    dim = args.dim or EMBED_DIM
    fa = FaissIndex(dim=dim, background_persist=getattr(args, 'background_persist', False), persist_interval=getattr(args, 'persist_interval', 60))
    n = fa.rebuild_from_chunk_embeddings(db, dim)
    print(f'Rebuilt FAISS from chunk embeddings: {n} entries')


def cmd_faiss_status(args):
    from interceptor.faiss_index import FaissIndex
    from interceptor.embeddings import EMBED_DIM
    fa = FaissIndex(dim=EMBED_DIM, background_persist=getattr(args, 'background_persist', False), persist_interval=getattr(args, 'persist_interval', 60))
    s = fa.status()
    import json

    print(json.dumps(s, indent=2))


def main():
    parser = argparse.ArgumentParser(prog='interceptor')
    sub = parser.add_subparsers()

    p = sub.add_parser('list', help='List captured messages')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('show', help='Show message content')
    p.add_argument('id')
    p.set_defaults(func=cmd_show)

    p = sub.add_parser('ingest', help='Run ingest worker once')
    p.add_argument('--limit', type=int, default=500)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser('tokenize', help='Run tokenize worker once')
    p.add_argument('--limit', type=int, default=500)
    p.add_argument('--encoding', type=str, default='gpt2')
    p.set_defaults(func=cmd_tokenize)

    p = sub.add_parser('migrate-chunks', help='Migrate existing messages into chunk store')
    p.add_argument('--limit', type=int, default=500)
    p.add_argument('--chunk-size', type=int, default=4096)
    p.set_defaults(func=cmd_migrate_chunks)

    p = sub.add_parser('search', help='Semantic search (query -> chunks/messages)')
    p.add_argument('query')
    p.add_argument('--k', type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser('proxy', help='Start mitmproxy/mitmdump with the interceptor addon')
    p.add_argument('--port', type=int, default=8080, help='Port to listen on')
    p.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind')
    p.add_argument('--mitmdump', action='store_true', help='Use mitmdump (non-interactive) instead of mitmproxy')
    p.set_defaults(func=cmd_proxy)

    p = sub.add_parser('proxy-stop', help='Stop mitmproxy/mitmdump by port or PID')
    p.add_argument('--port', type=int, default=None, help='Port the proxy is listening on')
    p.add_argument('--pid', type=int, default=None, help='PID to stop directly')
    p.set_defaults(func=cmd_proxy_stop)

    p = sub.add_parser('proxy-restart', help='Stop existing proxy on port and start a new one')
    p.add_argument('--port', type=int, default=8080, help='Port to listen on / to stop existing proxy')
    p.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind when starting new proxy')
    p.add_argument('--mitmdump', action='store_true', help='Use mitmdump (non-interactive) instead of mitmproxy')
    p.set_defaults(func=cmd_proxy_restart)

    p = sub.add_parser('mitm-ca', help='Show mitmproxy CA certificate path')
    p.add_argument('--all', action='store_true', help='List all candidate CA files in the mitmproxy dir')
    p.set_defaults(func=cmd_mitm_ca)

    p = sub.add_parser('proxy-export', help='Export captured messages as JSON')
    p.add_argument('--ids', type=str, help='Comma-separated message ids to export (overrides --limit)')
    p.add_argument('--limit', type=int, default=100, help='Number of recent messages to export')
    p.add_argument('--out', type=str, default=None, help='Output file path (defaults to stdout)')
    p.set_defaults(func=cmd_proxy_export)

    p = sub.add_parser('proxy-stats', help='Show compression, dedupe, tokenization and FAISS stats')
    p.add_argument('--json', action='store_true', help='Output JSON')
    p.set_defaults(func=cmd_proxy_stats)

    p = sub.add_parser('proxy-ca-install', help='Install mitmproxy CA into user trust store (best-effort)')
    p.add_argument('--cert', type=str, help='Path to CA cert (overrides detection)')
    p.add_argument('--yes', action='store_true', help='Do not prompt for confirmation')
    p.add_argument('--system', action='store_true', help='Attempt system-wide install (may require admin)')
    p.set_defaults(func=cmd_proxy_ca_install)

    p = sub.add_parser('faiss-rebuild', help='Rebuild FAISS index from chunk embeddings')
    p.add_argument('--dim', type=int, default=None, help='Embedding dimension (optional)')
    p.add_argument('--background-persist', action='store_true', help='Enable background persister for FAISS index')
    p.add_argument('--persist-interval', type=int, default=60, help='Background persister interval in seconds')
    p.set_defaults(func=cmd_faiss_rebuild)

    p = sub.add_parser('faiss-status', help='Show FAISS index status')
    p.add_argument('--background-persist', action='store_true', help='Enable background persister for FAISS index')
    p.add_argument('--persist-interval', type=int, default=60, help='Background persister interval in seconds')
    p.set_defaults(func=cmd_faiss_status)

    args = parser.parse_args()
    if not hasattr(args, 'func'):
        parser.print_help()
        return
    args.func(args)


if __name__ == '__main__':
    main()
