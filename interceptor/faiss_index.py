import os
import numpy as np
import tempfile
import time
import json
import threading
import atexit
import logging
from interceptor.db import DB
import faiss
_HAS_FAISS = True


class FaissIndex:
    """Light wrapper: prefer faiss if available, otherwise fall back to numpy-based index.

    The fallback supports add/search/rebuild but is not optimized for large corpora.
    """

    def __init__(self, dim: int = 512, index_path: str = './interceptor/faiss.index', mapping_path: str = './interceptor/faiss_map.json', background_persist: bool = False, persist_interval: int = 60):
        self.dim = dim
        self.index_path = index_path
        self.mapping_path = mapping_path
        self._mapping = []
        self._vectors = []
        self._db = DB()
        self._dirty = False
        self._meta_path = os.path.splitext(self.mapping_path)[0] + '_meta.json'
        self._save_lock = threading.Lock()
        self._background_persist = bool(background_persist)
        self._persist_interval = int(persist_interval)
        self._stop_event = None
        self._persist_thread = None
        self._logger = logging.getLogger(__name__)

        # Try to load persisted index/mapping; if not present, attempt rebuild; always ensure _index exists
        if _HAS_FAISS:
            try:
                if os.path.exists(self.index_path):
                    self._index = faiss.read_index(self.index_path)
                    if os.path.exists(self.mapping_path):
                        with open(self.mapping_path, 'r', encoding='utf-8') as f:
                            self._mapping = json.load(f)
                else:
                    # no persisted index — try to rebuild from chunk embeddings
                    try:
                        count = self.rebuild_from_chunk_embeddings(self._db, self.dim)
                        if not count:
                            self._index = faiss.IndexFlatL2(self.dim)
                    except Exception:
                        self._index = faiss.IndexFlatL2(self.dim)
            except Exception:
                self._index = faiss.IndexFlatL2(self.dim)
        else:
            self._index = None

        # start background persister if requested
        if self._background_persist:
            self._stop_event = threading.Event()
            self._persist_thread = threading.Thread(target=self._background_persist_worker, daemon=True)
            self._persist_thread.start()
            atexit.register(self.stop)

    def add(self, vectors: np.ndarray, message_ids=None):
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        n = vectors.shape[0]
        if message_ids is None:
            message_ids = [None] * n
        if _HAS_FAISS:
            self._index.add(vectors.astype('float32'))
        else:
            self._vectors.extend([v.astype('float32') for v in vectors])
        self._mapping.extend(message_ids)
        self._dirty = True
        # if background persister is enabled, let it flush; otherwise save immediately
        if not self._background_persist:
            self._save()

    def rebuild_from_chunk_embeddings(self, db: DB, dim: int):
        rows = db.get_all_chunk_embeddings()
        vecs = []
        keys = []
        for key, blob in rows:
            try:
                arr = np.frombuffer(blob, dtype='float32')
            except Exception:
                continue
            if arr.size != dim:
                continue
            vecs.append(arr)
            keys.append(key)
        if vecs:
            mat = np.vstack(vecs).astype('float32')
            if _HAS_FAISS:
                self._index = faiss.IndexFlatL2(dim)
                self._index.add(mat)
            else:
                self._vectors = [v for v in mat]
            self._mapping = keys
            # record reason and save
            try:
                tmp = self._meta_path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump({'last_rebuild_reason': 'rebuild_from_chunk_embeddings', 'last_rebuild_count': int(len(keys)), 'last_rebuild_ts': time.time()}, f)
                os.replace(tmp, self._meta_path)
            except Exception:
                pass
            self._dirty = True
            if not self._background_persist:
                self._save()
            return len(keys)
        return 0

    def status(self) -> dict:
        """Return a small status dict about the index and mapping."""
        idx_size = 0
        try:
            if _HAS_FAISS and self._index is not None:
                idx_size = int(self._index.ntotal)
        except Exception:
            idx_size = len(self._vectors)
        mapping_len = len(self._mapping)
        index_mtime = None
        index_size = None
        try:
            if os.path.exists(self.index_path):
                index_mtime = os.path.getmtime(self.index_path)
                try:
                    index_size = os.path.getsize(self.index_path)
                except Exception:
                    index_size = None
        except Exception:
            index_mtime = None
        # read meta if available
        last_rebuild_meta = None
        try:
            if os.path.exists(self._meta_path):
                with open(self._meta_path, 'r', encoding='utf-8') as f:
                    last_rebuild_meta = json.load(f)
        except Exception:
            last_rebuild_meta = None
        return {"dim": self.dim, "ntotal": idx_size, "mapping_len": mapping_len, "index_path": self.index_path, "mapping_path": self.mapping_path, "index_mtime": index_mtime, "index_size": index_size, "last_rebuild_meta": last_rebuild_meta}

    def search(self, vector: np.ndarray, k: int = 10):
        if _HAS_FAISS:
            D, idxs = self._index.search(vector.astype('float32'), k)
            hits = []
            for idx in idxs[0]:
                if idx < 0 or idx >= len(self._mapping):
                    hits.append(None)
                else:
                    hits.append(self._mapping[idx])
            return D, hits
        # fallback: brute-force L2
        if len(self._vectors) == 0:
            return np.array([[]]), [None] * k
        mat = np.vstack(self._vectors).astype('float32')
        vec = vector.astype('float32')
        diffs = mat - vec.reshape(1, -1)
        dists = np.sum(diffs * diffs, axis=1)
        idxs = np.argsort(dists)[:k]
        D = np.expand_dims(dists[idxs], axis=0)
        hits = [self._mapping[i] if i < len(self._mapping) else None for i in idxs]
        return D, hits

    def _save(self):
        # atomic write of index (if present) and mapping; write meta including success/error
        with self._save_lock:
            save_meta = {"last_saved_ts": time.time(), "last_save_success": False, "last_save_error": None}
        # write index
        try:
            if _HAS_FAISS and self._index is not None:
                dirp = os.path.dirname(self.index_path) or '.'
                fd, tmp = tempfile.mkstemp(dir=dirp)
                os.close(fd)
                try:
                    faiss.write_index(self._index, tmp)
                    os.replace(tmp, self.index_path)
                finally:
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
        except Exception as e:
            save_meta['last_save_error'] = f'index_write_error: {e}'
        # write mapping
        try:
            dirp = os.path.dirname(self.mapping_path) or '.'
            fd, tmpm = tempfile.mkstemp(dir=dirp)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self._mapping, f)
            os.replace(tmpm, self.mapping_path)
        except Exception as e:
            save_meta['last_save_error'] = (save_meta.get('last_save_error') or '') + f' mapping_write_error: {e}'
        # if no error recorded, mark success
        if not save_meta.get('last_save_error'):
            save_meta['last_save_success'] = True
        # write meta atomically
        try:
            dirp = os.path.dirname(self._meta_path) or '.'
            fdm, tmpmeta = tempfile.mkstemp(dir=dirp)
            with os.fdopen(fdm, 'w', encoding='utf-8') as f:
                json.dump(save_meta, f)
            os.replace(tmpmeta, self._meta_path)
        except Exception as e:
            # last resort: attempt non-atomic write
            try:
                with open(self._meta_path, 'w', encoding='utf-8') as f:
                    json.dump({**save_meta, 'last_save_error': (save_meta.get('last_save_error') or '') + f' meta_write_error: {e}'}, f)
            except Exception:
                pass
            self._dirty = False

    def rebuild_from_db(self, db: DB, dim: int):
        rows = db.get_all_embeddings()
        vecs = []
        mids = []
        for mid, blob in rows:
            try:
                arr = np.frombuffer(blob, dtype='float32')
            except Exception:
                continue
            if arr.size != dim:
                continue
            vecs.append(arr)
            mids.append(mid)
        if vecs:
            mat = np.vstack(vecs).astype('float32')
            if _HAS_FAISS:
                self._index = faiss.IndexFlatL2(dim)
                self._index.add(mat)
            else:
                self._vectors = [v for v in mat]
            self._mapping = mids
            if not self._background_persist:
                self._save()

    def _background_persist_worker(self):
        """Background thread: periodically persist if index/mapping are dirty."""
        self._logger.info("FAISS background persister started (interval=%ss)", self._persist_interval)
        try:
            while not (self._stop_event and self._stop_event.wait(self._persist_interval)):
                try:
                    if self._dirty:
                        self._logger.debug("FAISS background persister flushing changes")
                        self._save()
                except Exception:
                    # swallow to keep background thread alive
                    self._logger.exception("Error while persisting FAISS index in background")
            # final flush on exit
            if self._dirty:
                try:
                    self._save()
                except Exception:
                    self._logger.exception("Final FAISS save failed")
        except Exception:
            self._logger.exception("FAISS background persister exiting due to unexpected error")

    def stop(self, timeout: float = 5.0):
        """Stop background persister and flush any pending changes.

        Call on shutdown to ensure durable persistence.
        """
        if not self._background_persist:
            return
        if self._stop_event:
            self._stop_event.set()
        if self._persist_thread and self._persist_thread.is_alive():
            self._persist_thread.join(timeout)
        # final save if dirty
        if self._dirty:
            try:
                self._save()
            except Exception:
                self._logger.exception("Error saving FAISS index during stop()")
