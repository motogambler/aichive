import os
import time
import numpy as np
import argparse
from interceptor.db import DB
from interceptor.faiss_index import FaissIndex
from interceptor import compaction_worker
from tqdm import tqdm
from dotenv import load_dotenv
from interceptor.embeddings import get_embedding, EMBED_DIM

load_dotenv()

CFG_PATH = os.environ.get('INTERCEPTOR_CONFIG', './interceptor/config.yaml')


def run_once(limit: int = 500):
    db = DB()
    fa = FaissIndex(dim=EMBED_DIM)
    # Prefer chunk-level embedding if chunks exist
    chunk_hashes = db.get_unembedded_chunk_hashes(limit=limit)
    if chunk_hashes:
        vecs = []
        hashes = []
        for h in tqdm(chunk_hashes, desc='Embedding chunks'):
            content = db.get_chunk_bytes(h)
            try:
                text = content.decode('utf-8', errors='replace')
            except Exception:
                text = ''
            v = get_embedding(text)
            vecs.append(v)
            hashes.append(h)
        if vecs:
            mat = np.vstack(vecs).astype('float32')
            fa.add(mat, message_ids=hashes)
            for h, vec in zip(hashes, mat):
                db.add_chunk_embedding(h, vec.tobytes())
        print(f'Processed {len(hashes)} chunk embeddings')
        return

    # fallback to message-level embedding
    ids = db.get_unembedded_message_ids(limit=limit)
    if not ids:
        print('No new messages or chunks to embed')
        return
    vecs = []
    mids = []
    for mid in tqdm(ids, desc='Embedding'):
        content = db.get_message_content(mid)
        try:
            text = content.decode('utf-8', errors='replace')
        except Exception:
            text = ''
        v = get_embedding(text)
        vecs.append(v)
        mids.append(mid)
    if vecs:
        mat = np.vstack(vecs).astype('float32')
        fa.add(mat, message_ids=mids)
        # store vectors in DB in same order
        for mid, vec in zip(mids, mat):
            db.add_embedding(mid, vec.tobytes())
    print(f'Processed {len(mids)} message embeddings')


def run_once_with_compaction(limit: int = 500):
    """Run embedding pass and then trigger a compaction pass (non-destructive)."""
    run_once(limit=limit)
    try:
        db = DB()
        # ensure embeddings
        added = 0
        ids = db.get_unembedded_message_ids(limit=limit)
        for mid in ids:
            content = db.get_message_content(mid)
            try:
                text = content.decode('utf-8', errors='replace')
            except Exception:
                text = ''
            vec = get_embedding(text)
            db.add_embedding(mid, vec.tobytes())
            added += 1
        if added:
            print(f'Added {added} embeddings before compaction')
        ids, mat = compaction_worker.load_embeddings(db)
        groups = compaction_worker.group_near_duplicates(ids, mat)
        created = compaction_worker.create_compactions(db, groups)
        print(f'Compaction entries created: {created}')
    except Exception as e:
        print('Compaction pass failed:', e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--with-compaction', action='store_true', help='Run a compaction pass after embedding')
    parser.add_argument('--limit', type=int, default=500)
    args = parser.parse_args()
    if args.once:
        if args.with_compaction:
            run_once_with_compaction(limit=args.limit)
        else:
            run_once(limit=args.limit)
    else:
        while True:
            if args.with_compaction:
                run_once_with_compaction(limit=args.limit)
            else:
                run_once(limit=args.limit)
            time.sleep(5)


if __name__ == '__main__':
    main()
