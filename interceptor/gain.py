import argparse
import json
import zlib
from interceptor.db import DB
from interceptor.token_compact import encode_to_tokens


def approx_tokens_from_text(text: str) -> int:
    try:
        enc, arr = encode_to_tokens(text)
        return int(arr.size)
    except Exception:
        return max(1, len(text) // 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true', help='Output JSON')
    args = parser.parse_args()

    db = DB()
    rows = db.get_all_messages()
    total_messages = len(rows)
    raw_tokens = 0
    stored_compressed_bytes = 0
    wire_estimate_tokens = 0

    for r in rows:
        mid = r[0]
        compressed_blob = r[6]
        # try to get compressed size (may be encrypted)
        try:
            dec = db._maybe_decrypt(compressed_blob) if compressed_blob else b""
            stored_compressed_bytes += len(dec or b"")
        except Exception:
            stored_compressed_bytes += 0

        # token counts: prefer stored tokenized value
        tok = None
        try:
            trow = db.get_tokenized(mid)
            if trow:
                tok = int(trow[2])
        except Exception:
            tok = None

        if tok is None:
            try:
                content = db.get_message_content(mid)
                text = content.decode('utf-8', errors='replace')
            except Exception:
                text = ''
            tok = approx_tokens_from_text(text)

        raw_tokens += tok
        # approximate wire tokens as compressed bytes / 4
        wire_estimate_tokens += max(1, stored_compressed_bytes // 4)

    comps = db.get_compactions()
    compaction_entries = len(comps)

    out = {
        'messages': total_messages,
        'raw_tokens_est': raw_tokens,
        'wire_tokens_est': wire_estimate_tokens,
        'stored_compressed_bytes': stored_compressed_bytes,
        'compaction_entries': compaction_entries,
    }

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Messages: {total_messages}")
        print(f"Raw tokens (est): {raw_tokens}")
        print(f"Wire tokens (est): {wire_estimate_tokens}")
        if raw_tokens:
            saved = raw_tokens - wire_estimate_tokens
            print(f"Estimated token savings: {saved} ({saved/raw_tokens*100:.1f}% )")
        print(f"Stored compressed bytes: {stored_compressed_bytes}")
        print(f"Compaction entries: {compaction_entries}")


if __name__ == '__main__':
    main()
