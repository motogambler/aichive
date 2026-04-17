"""Embedding helpers extracted to avoid circular imports.

Provides a deterministic local embedding fallback and exposes the
`EMBED_DIM` constant used across the package.
"""
import os
import hashlib
import numpy as np

# Default embed dim (can be overridden via env)
EMBED_DIM = int(os.environ.get('EMBED_DIM', '512'))


def _deterministic_vector(text: str, dim: int = EMBED_DIM):
    h = hashlib.sha256(text.encode('utf-8')).digest()
    seed = int.from_bytes(h[:8], 'big') % (2**32)
    rnd = np.random.RandomState(seed)
    return rnd.rand(dim).astype('float32')


def get_embedding(text: str):
    """Return a deterministic local embedding for `text`.

    This is the default behavior for local development / tests and is
    intentionally deterministic to make unit tests stable.
    """
    return _deterministic_vector(text, dim=EMBED_DIM)
