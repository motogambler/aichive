import io
import tokenize
from typing import Optional


def compress_python_source(src: str) -> str:
    """Strip comments and excessive blank lines from Python source.

    This is a lightweight AST-aware compressor using the stdlib tokenizer.
    It is not a perfect AST rewrite but removes comments and normalizes spacing
    to produce a compact representation suitable for wire restructuring.
    """
    out_lines = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        prev_end_row = -1
        line_buf = ''
        for tok_type, tok_str, start, end, _ in tokens:
            if tok_type == tokenize.COMMENT:
                continue
            if tok_type == tokenize.NL:
                continue
            if tok_type == tokenize.NEWLINE:
                if line_buf.strip():
                    out_lines.append(line_buf.rstrip())
                line_buf = ''
                prev_end_row = end[0]
                continue
            # accumulate token strings with a single space separator
            if line_buf and not line_buf.endswith(' '):
                line_buf += ' '
            line_buf += tok_str
        # flush
        if line_buf.strip():
            out_lines.append(line_buf.rstrip())
    except Exception:
        # fallback: naive whitespace collapse
        return '\n'.join([l.strip() for l in src.splitlines() if l.strip()])

    # remove consecutive duplicate blank lines already handled; join
    return '\n'.join(out_lines)


def compress_code(text: str, lang: Optional[str] = None) -> str:
    """Compress code-like text.

    If `lang` is 'python' or heuristically detected, apply Python-specific
    token-based compression. Otherwise, fall back to collapsing blank lines.
    """
    if not text:
        return text
    if lang == 'python' or ('\ndef ' in text) or text.lstrip().startswith('def ') or 'class ' in text:
        return compress_python_source(text)
    # generic fallback: remove repeated blank lines
    return '\n'.join([l.rstrip() for l in text.splitlines() if l.strip()])


__all__ = ['compress_code', 'compress_python_source']
