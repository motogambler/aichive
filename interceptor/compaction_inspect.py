"""CLI to inspect compaction entries and provenance."""
import argparse
import json
import time
from interceptor.db import DB


def human_ts(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
    except Exception:
        return str(ts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    db = DB()
    rows = db.get_compactions()
    rows = rows[: args.limit]
    out = []
    for r in rows:
        cid, ts, summary, mids = r
        prov = None
        if '[summary_provenance]' in summary:
            try:
                parts = summary.rsplit('[summary_provenance]', 1)
                summary_text = parts[0].strip()
                prov = json.loads(parts[1])
            except Exception:
                summary_text = summary
        else:
            summary_text = summary
        entry = {
            'id': cid,
            'ts': ts,
            'ts_human': human_ts(ts),
            'summary_len': len(summary_text),
            'message_ids': mids,
            'provenance': prov,
        }
        out.append(entry)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        for e in out:
            print(f"[{e['id']}] {e['ts_human']} len={e['summary_len']} prov={e['provenance']}")


if __name__ == '__main__':
    main()
