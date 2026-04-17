"""Recall CLI: build token-budgeted context bundles from compaction entries.

Usage:
  python recall.py --budget 2048 [--format text|json] [--query QUERY]

Behavior:
  - If `--query` is provided, rank compaction summaries by simple substring score
    and include the best matches until the budget is exhausted.
  - Otherwise include most-recent compaction entries until budget.
  - Falls back to recent raw messages if no compactions exist.
"""
import argparse
import json
from interceptor.db import DB
from interceptor.token_compact import encode_to_tokens


def approx_tokens(text: str) -> int:
    # Prefer exact tokenization via encode_to_tokens; fall back to heuristic
    try:
        enc, arr = encode_to_tokens(text)
        return int(arr.size)
    except Exception:
        return max(1, len(text) // 4)


def build_bundle(db: DB, budget: int, query: str = None):
    comps = db.get_compactions()
    bundle = []
    used = 0
    if comps:
        # comps is list of tuples (id, ts, summary, message_ids)
        if query:
            # simple score: count occurrences of query in summary
            scored = []
            for cid, ts, summary, mids in comps:
                score = summary.lower().count(query.lower())
                scored.append((score, cid, ts, summary))
            scored.sort(reverse=True, key=lambda x: x[0])
            items = [(c[1], c[3]) for c in scored if c[0] > 0]
        else:
            # most recent first
            items = [(c[0], c[2]) for c in comps]
            items = [(cid, summary) for cid, ts, summary, mids in comps]
        for cid, summary in items:
            t = approx_tokens(summary)
            if used + t > budget:
                continue
            bundle.append({'source': f'compaction:{cid}', 'tokens': t, 'text': summary})
            used += t
        # if bundle empty and query provided, fall back to recent compactions
        if not bundle and query:
            for cid, ts, summary, mids in comps:
                t = approx_tokens(summary)
                if used + t > budget:
                    continue
                bundle.append({'source': f'compaction:{cid}', 'tokens': t, 'text': summary})
                used += t
    # fallback to raw messages if still empty
    if not bundle:
        rows = db.get_all_messages()
        for r in rows:
            mid = r[0]
            text = db.get_message_content(mid).decode('utf-8', errors='replace')
            t = approx_tokens(text)
            if used + t > budget:
                continue
            bundle.append({'source': f'message:{mid}', 'tokens': t, 'text': text[:2000]})
            used += t
            if used >= budget:
                break
    return bundle, used


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--budget', type=int, default=2048)
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    parser.add_argument('--query', type=str, default=None)
    args = parser.parse_args()
    db = DB()
    bundle, used = build_bundle(db, args.budget, query=args.query)
    if args.format == 'json':
        print(json.dumps({'tokens_used': used, 'entries': bundle}, indent=2))
    else:
        out = []
        out.append(f'# Cross-session context ({used} tokens)')
        for e in bundle:
            out.append(f"## Source: {e['source']} (tokens: {e['tokens']})")
            out.append(e['text'])
            out.append('\n')
        print('\n'.join(out))


if __name__ == '__main__':
    main()
