"""Compaction worker: groups near-duplicate messages and creates compaction entries.

Usage:
  python compaction_worker.py --threshold 0.95 [--apply-prune]

This script is safe by default (it creates compaction entries but does not delete
messages). Pass `--apply-prune` to remove deduplicated message rows (keeps earliest).
"""
import os
import argparse
import json
import numpy as np
from .db import DB
from interceptor.embeddings import get_embedding, EMBED_DIM

import faiss as _faiss
import openai


def _summarize_parts_with_llm(parts):
    # simple prompt combining parts; guard token sizes by truncation
    prompt_parts = []
    for p in parts:
        prompt_parts.append(p[:4000])
    user = "\n\n---\n\n".join(prompt_parts)
    # support multiple prompt templates selectable via env var
    TEMPLATES = {
        'default': {
            'system': 'You are a concise summarizer. Produce a short synthesis capturing key points.',
            'user_preface': 'Synthesize these notes:'
        },
        'bullet': {
            'system': 'You are a summarizer. Return a short bulleted list of main facts.',
            'user_preface': 'Create bullets:'
        },
        'technical': {
            'system': 'You are a technical summarizer. Produce a precise, technical summary with key actions and parameters.',
            'user_preface': 'Technical synthesis:'
        }
    }

    try:
        if openai is None or not os.environ.get('OPENAI_API_KEY'):
            return None
        openai.api_key = os.environ.get('OPENAI_API_KEY')
        model = os.environ.get('OPENAI_SUMMARY_MODEL', 'gpt-3.5-turbo')
        max_tokens = int(os.environ.get('OPENAI_SUMMARY_MAX_TOKENS', '256'))
        tone = os.environ.get('OPENAI_SUMMARY_TONE', 'concise')
        template = os.environ.get('OPENAI_SUMMARY_TEMPLATE', 'default')
        tmpl = TEMPLATES.get(template, TEMPLATES['default'])
        # craft a slightly richer system prompt with provenance and style hints
        system_msg = f"{tmpl.get('system')} Tone: {tone}. Keep summary <= {max_tokens} tokens."
        user_msg = tmpl.get('user_preface', '') + "\n\n" + user
        # Try ChatCompletion path first
        try:
            resp = openai.ChatCompletion.create(
                model=model,
                messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
                max_tokens=max_tokens,
                temperature=float(os.environ.get('OPENAI_SUMMARY_TEMPERATURE', '0.0')),
            )
            txt = resp['choices'][0]['message']['content'].strip()
            # append provenance stub
            prov = json.dumps({"model": model, "max_tokens": max_tokens, "sources": len(parts)})
            return txt + "\n\n[summary_provenance]" + prov
        except Exception:
            try:
                # fallback to legacy completion API
                resp = openai.Completion.create(model='text-davinci-003', prompt=system_msg + "\n\n" + user_msg, max_tokens=max_tokens)
                txt = resp['choices'][0]['text'].strip()
                prov = json.dumps({"model": 'text-davinci-003', "max_tokens": max_tokens, "sources": len(parts)})
                return txt + "\n\n[summary_provenance]" + prov
            except Exception:
                return None
    except Exception:
        return None


def summarize_group(parts):
    """Return a short summary for a group of message parts.

    Attempts to use an LLM (OpenAI) if configured; otherwise falls back to
    a deterministic heuristic concatenation that preserves provenance.
    """
    # try LLM first
    llm_summary = _summarize_parts_with_llm(parts)
    if llm_summary:
        return llm_summary
    # deterministic fallback: join heads with separators and truncate
    joined = "\n\n---\n\n".join(p[:1200] for p in parts)
    return joined[:8000]

DEFAULT_DIM = EMBED_DIM


def load_embeddings(db: DB):
    rows = db.get_all_embeddings()
    ids = []
    mats = []
    for mid, blob in rows:
        try:
            arr = np.frombuffer(blob, dtype='float32')
        except Exception:
            continue
        ids.append(mid)
        mats.append(arr)
    if not mats:
        return [], np.empty((0, DEFAULT_DIM), dtype='float32')
    mat = np.vstack(mats).astype('float32')
    return ids, mat


def ensure_embeddings_for_messages(db: DB):
    ids = db.get_unembedded_message_ids(limit=10000)
    if not ids:
        return 0
    count = 0
    for mid in ids:
        content = db.get_message_content(mid)
        try:
            text = content.decode('utf-8', errors='replace')
        except Exception:
            text = ''
        vec = get_embedding(text)
        db.add_embedding(mid, vec.tobytes())
        count += 1
    return count


def group_near_duplicates(ids, mat, threshold=0.95):
    """Group indices into clusters where cosine similarity >= threshold."""
    if mat.shape[0] == 0:
        return []
    # normalize
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matn = mat / norms
    dim = matn.shape[1]
    # use faiss if available for faster neighbor search
    if _faiss is not None:
        try:
            index = _faiss.IndexFlatIP(dim)
            index.add(matn.astype('float32'))
            dists, inds = index.search(matn.astype('float32'), 16)
        except Exception:
            # fallback to brute-force
            prod = matn.dot(matn.T)
            np.fill_diagonal(prod, -np.inf)
            inds = np.argsort(-prod, axis=1)[:, :16]
            dists = -np.sort(-prod, axis=1)[:, :16]
    else:
        # fallback: brute force cosine similarity via dot products
        prod = matn.dot(matn.T)
        # set self similarity to -inf to avoid self-match handling later
        np.fill_diagonal(prod, -np.inf)
        inds = np.argsort(-prod, axis=1)[:, :16]
        dists = -np.sort(-prod, axis=1)[:, :16]
    n = matn.shape[0]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j_idx, score in zip(inds[i], dists[i]):
            if j_idx == -1:
                continue
            if float(score) >= threshold and i != j_idx:
                union(i, j_idx)

    clusters = {}
    for i in range(n):
        r = find(i)
        clusters.setdefault(r, []).append(i)
    # convert clusters of indices to clusters of ids
    groups = []
    for members in clusters.values():
        if len(members) > 0:
            group_ids = [ids[m] for m in members]
            groups.append(group_ids)
    return groups


def create_compactions(db: DB, groups):
    created = 0
    for g in groups:
        if len(g) <= 1:
            continue
        # build a lightweight summary from constituent messages
        parts = []
        for mid in g:
            try:
                txt = db.get_message_content(mid).decode('utf-8', errors='replace')
            except Exception:
                txt = ''
            parts.append(txt)
        # prefer LLM synthesis when available, else deterministic concat
        summary = summarize_group(parts)
        db.add_compaction(summary, g)
        created += 1
    return created


def prune_duplicates(db: DB, groups):
    deleted = 0
    for g in groups:
        if len(g) <= 1:
            continue
        # keep earliest by message ts
        c = db._conn.cursor()
        q = f"SELECT id, ts FROM messages WHERE id IN ({','.join(['?']*len(g))}) ORDER BY ts ASC"
        c.execute(q, tuple(g))
        rows = c.fetchall()
        if not rows:
            continue
        to_delete = [r[0] for r in rows[1:]]
        if to_delete:
            db.delete_messages_by_ids(to_delete)
            deleted += len(to_delete)
    return deleted


def run_once(threshold: float = 0.95, apply_prune: bool = False):
    db = DB()
    # ensure embeddings exist
    added = ensure_embeddings_for_messages(db)
    if added:
        print(f'Added {added} embeddings')
    ids, mat = load_embeddings(db)
    if mat.shape[0] == 0:
        print('No embeddings available to cluster')
        return
    groups = group_near_duplicates(ids, mat, threshold=threshold)
    print(f'Found {len(groups)} groups (including singletons)')
    created = create_compactions(db, groups)
    print(f'Created {created} compaction entries')
    if apply_prune:
        deleted = prune_duplicates(db, groups)
        print(f'Deleted {deleted} duplicate messages')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=0.95)
    parser.add_argument('--apply-prune', action='store_true')
    args = parser.parse_args()
    run_once(threshold=args.threshold, apply_prune=args.apply_prune)


if __name__ == '__main__':
    main()
