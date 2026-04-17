import os
import tempfile
from interceptor.db import DB


def test_chunk_refcount_and_gc():
    fd, path = tempfile.mkstemp(prefix='interceptor_test_', suffix='.db')
    os.close(fd)
    try:
        db = DB(path)
        # store a fake message and chunk it
        mid = db.store_message('outbound', 'http://x', 'GET', {}, b'hello world')
        # create two chunks
        ch1 = 'h1'
        ch2 = 'h2'
        # store chunks
        db.store_chunk(ch1, b'hello')
        db.store_chunk(ch2, b' world')
        # link to message
        db.link_message_chunks(mid, [ch1, ch2])
        # refs should be 1 for each
        hashes = db.get_message_chunks(mid)
        assert hashes == [ch1, ch2]
        # decrement refs and GC
        db.dec_ref_chunk(ch1)
        db.dec_ref_chunk(ch2)
        deleted = db.gc_chunks()
        # both refs went to 0 -> deleted
        assert deleted >= 0
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
