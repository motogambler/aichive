import numpy as np
from interceptor.token_compact import compress_tokens, decompress_tokens


def test_varint_zlib_roundtrip():
    arr = np.array([1, 3, 10, 10, 200, 1000], dtype='uint32')
    blob = compress_tokens(arr, method='zlib', use_varint=True)
    out = decompress_tokens(blob, method='zlib', use_varint=True)
    assert out.dtype == arr.dtype
    assert out.tolist() == arr.tolist()


def test_lz4_roundtrip():
    arr = np.array([5, 6, 7, 100000], dtype='uint32')
    blob = compress_tokens(arr, method='lz4', use_varint=False)
    out = decompress_tokens(blob, method='lz4', use_varint=False)
    assert out.tolist() == arr.tolist()


def test_brotli_roundtrip():
    arr = np.array([0, 1, 2, 3, 4], dtype='uint32')
    blob = compress_tokens(arr, method='brotli', use_varint=False)
    out = decompress_tokens(blob, method='brotli', use_varint=False)
    assert out.tolist() == arr.tolist()
