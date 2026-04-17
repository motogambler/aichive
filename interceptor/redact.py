import os
import re
from typing import List, Tuple

from interceptor.redact_policy import load_policy

# Basic PII redaction enabled by default; can be disabled with env var
REDACT_PII = os.environ.get('INTERCEPTOR_REDACT_PII', '1') not in ('0', 'false', 'False')

# Default fallback patterns
DEFAULT_PATTERNS = [
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), '<REDACTED-EMAIL>'),
    (re.compile(r"sk-[A-Za-z0-9-_]{20,}"), '<REDACTED-API-KEY>'),
    (re.compile(r"AKIA[0-9A-Z]{16}"), '<REDACTED-AWS-KEY>'),
    (re.compile(r"[A-Fa-f0-9]{32,}"), '<REDACTED-HEX>'),
    (re.compile(r"eyJ[A-Za-z0-9-_\.]+\.[A-Za-z0-9-_\.]+\.[A-Za-z0-9-_\.]+"), '<REDACTED-JWT>'),
]


def _compile_policy() -> List[Tuple[re.Pattern, str]]:
    rules = []
    try:
        policy = load_policy()
        for pat, repl in policy:
            try:
                rules.append((re.compile(pat), repl))
            except Exception:
                continue
    except Exception:
        policy = []
    if not rules:
        return DEFAULT_PATTERNS
    return rules


_COMPILED = _compile_policy()


def redact_text(text: str) -> str:
    """Return a redacted copy of `text` by applying policy rules then defaults.

    If redaction is disabled via `INTERCEPTOR_REDACT_PII=0`, returns text unchanged.
    """
    if not REDACT_PII or not text:
        return text
    out = text
    for pat, repl in _COMPILED:
        out = pat.sub(repl, out)
    return out


__all__ = ['redact_text']
