import os
import tempfile
from interceptor.db import DB
from interceptor.ingest_worker import get_embedding, EMBED_DIM
from interceptor.faiss_index import FaissIndex


def test_faiss_add_and_search():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        db = DB(path)
        # add three messages
        mids = []
        for t in ['hello world', 'another message', 'copilot prompt']:
            mid = db.store_message('out', 'http://x', 'POST', {}, t.encode('utf-8'))
            mids.append(mid)
        # compute embeddings and add to faiss
        vecs = [get_embedding(db.get_message_content(mid).decode('utf-8')) for mid in mids]
        fa = FaissIndex(dim=EMBED_DIM, index_path=path + '.index', mapping_path=path + '.map.json')
        import numpy as np

        mat = np.vstack(vecs).astype('float32')
        fa.add(mat, message_ids=mids)
        # search for 'hello'
        q = get_embedding('hello')
        D, hits = fa.search(q.reshape(1, -1), 3)
        assert any(h in mids for h in hits if h is not None)
    finally:
        try:
            db.close()
        except Exception:
            pass
        import time
        try:
            for _ in range(20):
                try:
                    os.remove(path)
                    break
                except PermissionError:
                    time.sleep(0.1)
            else:
                try:
                    os.remove(path)
                except PermissionError:
                    pass
        except Exception:
            pass
        try:
            os.remove(path + '.index')
        except Exception:
            pass
        try:
            os.remove(path + '.map.json')
        except Exception:
            pass
