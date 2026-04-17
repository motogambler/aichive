import os
import tempfile
from interceptor.db import DB
from cryptography.fernet import Fernet


def test_store_and_retrieve_plain():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        db = DB(path)
        mid = db.store_message('outbound', 'http://example.com', 'GET', {'h': 'v'}, b'hello world')
        content = db.get_message_content(mid)
        assert content == b'hello world'
    finally:
        try:
            db.close()
        except Exception:
            pass
        # Retry remove on Windows transient locks
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
                    # give up on Windows transient lock; leave temp file
                    pass
        except Exception:
            pass


def test_encryption_roundtrip():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    key = Fernet.generate_key().decode()
    os.environ['INTERCEPTOR_KEY'] = key
    try:
        db = DB(path)
        mid = db.store_message('inbound', 'http://example.com', 'POST', {}, b'secret')
        content = db.get_message_content(mid)
        assert content == b'secret'
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
        os.environ.pop('INTERCEPTOR_KEY', None)
