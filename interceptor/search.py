import argparse
import json
import numpy as np
from interceptor.db import DB
from interceptor.faiss_index import FaissIndex
from interceptor.embeddings import get_embedding, EMBED_DIM


def semantic_search(query: str, k: int = 5):
    db = DB()
    fa = FaissIndex(dim=EMBED_DIM)
    v = get_embedding(query)
    D, hits = fa.search(np.expand_dims(v, 0), k=k)
    out = []
    for dist, h in zip(D[0], hits):
        if h is None:
            continue
        # h may be chunk_hash (str) or numeric message id
        if isinstance(h, str):
            chunk_hash = h
            msgs = []
            for row in db.get_all_messages():
                mid = row[0]
                chs = db.get_message_chunks(mid)
                if chunk_hash in chs:
                    msgs.append(mid)
            snippet_bytes = db.get_chunk_bytes(chunk_hash)[:512]
            try:
                snippet = snippet_bytes.decode('utf-8', errors='replace')
            except Exception:
                snippet = repr(snippet_bytes)
            out.append({'chunk': chunk_hash, 'messages': msgs, 'dist': float(dist), 'snippet': snippet})
        else:
            mid = h
            snippet_bytes = db.rehydrate_message(mid)[:512]
            try:
                snippet = snippet_bytes.decode('utf-8', errors='replace')
            except Exception:
                snippet = repr(snippet_bytes)
            out.append({'message': mid, 'dist': float(dist), 'snippet': snippet})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('query')
    parser.add_argument('--k', type=int, default=5)
    args = parser.parse_args()
    res = semantic_search(args.query, k=args.k)
    print(json.dumps(res, indent=2))


if __name__ == '__main__':
    main()
