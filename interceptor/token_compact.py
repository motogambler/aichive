import zlib
import numpy as np
from typing import Tuple, List
import tiktoken
import brotli
import lz4.frame as lz4f
HAS_TIKTOKEN = True


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        towrite = n & 0x7F
        n >>= 7
        if n:
            out.append(towrite | 0x80)
        else:
            out.append(towrite)
            break
    return bytes(out)


def _decode_varints(b: bytes) -> List[int]:
    vals = []
    n = 0
    shift = 0
    for byte in b:
        n |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        # interpret as unsigned 32-bit then convert to signed int32
        if n & (1 << 31):
            signed = n - (1 << 32)
        else:
            signed = n
        vals.append(signed)
        n = 0
        shift = 0
    return vals


def _deltas_from_array(arr: np.ndarray) -> List[int]:
    if arr.size == 0:
        return []
    prev = 0
    deltas = []
    for v in arr.tolist():
        d = int(v) - int(prev)
        deltas.append(d)
        prev = int(v)
    return deltas


def _array_from_deltas(deltas: List[int]) -> np.ndarray:
    vals = []
    total = 0
    for d in deltas:
        total = int(total) + int(d)
        vals.append(int(total))
    # create as int64 then cast to uint32 to avoid C long overflow on some platforms
    return np.array(vals, dtype='int64').astype('uint32')


def _bytes_to_uint32_array(b: bytes) -> np.ndarray:
    # pack bytes into uint32 little-endian
    pad = (4 - (len(b) % 4)) % 4
    if pad:
        b = b + b"\x00" * pad
    arr = np.frombuffer(b, dtype="uint8")
    arr32 = arr.reshape(-1, 4).view("uint32").reshape(-1)
    return arr32


def _uint32_array_to_bytes(arr: np.ndarray) -> bytes:
    b = arr.astype("uint32").tobytes()
    return b.rstrip(b"\x00")


def encode_to_tokens(text: str, encoding_name: str = "gpt2") -> Tuple[str, np.ndarray]:
    """Encode text to token ids and return (encoding_name, numpy array of uint32).

    If `tiktoken` is available, use it. Otherwise fall back to a lossless
    UTF-8 byte packing scheme (`utf8-bytes`) which packs raw bytes into
    uint32 tokens and can be decoded back exactly.
    """
    if HAS_TIKTOKEN:
        enc = tiktoken.get_encoding(encoding_name)
        ids: List[int] = enc.encode(text)
        arr = np.array(ids, dtype="uint32")
        return encoding_name, arr
    else:
        b = text.encode("utf-8")
        arr32 = _bytes_to_uint32_array(b)
        return "utf8-bytes", arr32


def compress_token_array(arr: np.ndarray) -> bytes:
    # legacy default: zlib
    return zlib.compress(arr.tobytes())


def compress_tokens(arr: np.ndarray, method: str = 'zlib', use_varint: bool = False) -> bytes:
    if use_varint:
        deltas = _deltas_from_array(arr)
        # encode each delta as varint
        b = b''.join([_encode_varint(d if d >= 0 else (d & 0xFFFFFFFF)) for d in deltas])
    else:
        b = arr.tobytes()
    if method == 'zlib':
        return zlib.compress(b)
    elif method == 'brotli':
        if brotli is None:
            # fallback to zlib if brotli not installed
            return zlib.compress(b)
        return brotli.compress(b)
    elif method == 'lz4':
        if lz4f is None:
            # fallback to zlib if lz4 not installed
            return zlib.compress(b)
        return lz4f.compress(b)
    else:
        raise ValueError('unknown compression method')


def decompress_token_array(blob: bytes) -> np.ndarray:
    raw = zlib.decompress(blob)
    arr = np.frombuffer(raw, dtype="uint32")
    return arr


def decompress_tokens(blob: bytes, method: str = 'zlib', use_varint: bool = False) -> np.ndarray:
    if method == 'zlib':
        b = zlib.decompress(blob)
    elif method == 'brotli':
        if brotli is None:
            # blob was likely zlib-compressed as fallback
            try:
                b = zlib.decompress(blob)
            except Exception:
                raise
        else:
            b = brotli.decompress(blob)
    elif method == 'lz4':
        if lz4f is None:
            try:
                b = zlib.decompress(blob)
            except Exception:
                raise
        else:
            b = lz4f.decompress(blob)
    else:
        raise ValueError('unknown compression method')
    if use_varint:
        deltas = _decode_varints(b)
        arr = _array_from_deltas(deltas)
        return arr
    else:
        return np.frombuffer(b, dtype='uint32')


def decode_from_tokens(arr: np.ndarray, encoding_name: str = "gpt2") -> str:
    if HAS_TIKTOKEN and encoding_name != "utf8-bytes":
        enc = tiktoken.get_encoding(encoding_name)
        return enc.decode(arr.tolist())
    # fallback: treat as packed utf-8 bytes
    b = _uint32_array_to_bytes(arr)
    return b.decode("utf-8", errors="replace")
