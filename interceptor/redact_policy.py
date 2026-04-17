import os
import yaml
from typing import List, Tuple

DEFAULT_POLICY_PATH = os.environ.get('INTERCEPTOR_REDACT_POLICY', './interceptor/redact.yaml')


def load_policy(path: str = None) -> List[Tuple[str, str]]:
    p = path or DEFAULT_POLICY_PATH
    try:
        with open(p, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return []
    rules = cfg.get('rules', [])
    out = []
    for r in rules:
        pat = r.get('pattern')
        repl = r.get('replace', '<REDACTED>')
        if pat:
            out.append((pat, repl))
    return out


def save_policy(rules: List[dict], path: str = None) -> None:
    p = path or DEFAULT_POLICY_PATH
    cfg = {'rules': rules}
    with open(p, 'w', encoding='utf-8') as f:
        yaml.safe_dump(cfg, f)


__all__ = ['load_policy', 'save_policy']
