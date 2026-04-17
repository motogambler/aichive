import argparse
from interceptor.db import DB
from interceptor.token_compact import encode_to_tokens, compress_tokens
from tqdm import tqdm


def run_once(limit: int = 500, encoding: str = 'gpt2'):
    db = DB()
    # prefer chunk-level tokenization
    chunk_hashes = db.get_un_tokenized_chunk_hashes(limit=limit)
    if chunk_hashes:
        ids = chunk_hashes
        is_chunk = True
    else:
        ids = db.get_untokenized_message_ids(limit=limit)
        is_chunk = False
    if not ids:
        print('No new messages or chunks to tokenize')
        return
    # load token config
    import os
    import yaml
    cfg_path = os.environ.get('INTERCEPTOR_CONFIG', './interceptor/config.yaml')
    cfg = {}
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        pass
    token_cfg = cfg.get('tokens', {})
    method = token_cfg.get('compression', 'zlib')
    use_varint = bool(token_cfg.get('varint_deltas', True))
    enc_name = token_cfg.get('encoding', encoding)

    for mid in tqdm(ids, desc='Tokenizing'):
        if is_chunk:
            content = db.get_chunk_bytes(mid)
        else:
            content = db.get_message_content(mid)
        try:
            text = content.decode('utf-8', errors='replace')
        except Exception:
            text = ''
        enc_name, arr = encode_to_tokens(text, encoding_name=enc_name)
        blob = compress_tokens(arr, method=method, use_varint=use_varint)
        if is_chunk:
            db.store_chunk_tokenized(mid, enc_name, blob, int(arr.size))
        else:
            db.store_tokenized(mid, enc_name, blob, int(arr.size))
    print(f'Processed {len(ids)} messages')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--limit', type=int, default=500)
    parser.add_argument('--encoding', type=str, default='gpt2')
    args = parser.parse_args()
    if args.once:
        run_once(limit=args.limit, encoding=args.encoding)
    else:
        while True:
            run_once(limit=args.limit, encoding=args.encoding)


if __name__ == '__main__':
    main()
