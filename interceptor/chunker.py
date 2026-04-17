import hashlib

def chunk_bytes(data: bytes, chunk_size: int = 8192):
    """Yield fixed-size chunks from data."""
    if not data:
        return []
    out = []
    for i in range(0, len(data), chunk_size):
        out.append(data[i:i+chunk_size])
    return out

def chunk_hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
