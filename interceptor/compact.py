"""Simple compaction summary tool.

This is a safe, read-only (by default) compaction pass that computes
deduplication and merge statistics. It does not delete raw messages unless
`--apply-prune` is provided.
"""
import argparse
from interceptor.db import DB


def compact_summary(db: DB, apply_prune: bool = False):
    rows = db.get_all_messages()
    total = len(rows)
    # group by hash (sha256 of original content)
    groups = {}
    for r in rows:
        mid = r[0]
        h = r[7]
        groups.setdefault(h, []).append(mid)

    deduped = 0
    dup_groups = 0
    for h, ids in groups.items():
        if len(ids) > 1:
            dup_groups += 1
            deduped += len(ids) - 1
    print(f"Messages total: {total}")
    print(f"Duplicate groups: {dup_groups}")
    print(f"Potential deduped messages: {deduped}")

    # show top duplicated hashes (up to 10)
    top = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]
    for h, ids in top:
        print(f"hash={h} count={len(ids)} sample_ids={ids[:5]}")

    if apply_prune:
        # WARNING: this will delete duplicate message rows and associated embeddings/tokenized
        # Keep the first id in each group and delete the rest
        deleted = 0
        for h, ids in groups.items():
            if len(ids) <= 1:
                continue
            to_delete = ids[1:]
            # delete from embeddings/tokenized/messages
            c = db._conn.cursor()
            c.executemany("DELETE FROM embeddings WHERE message_id = ?", [(i,) for i in to_delete])
            c.executemany("DELETE FROM tokenized WHERE message_id = ?", [(i,) for i in to_delete])
            c.executemany("DELETE FROM messages WHERE id = ?", [(i,) for i in to_delete])
            db._conn.commit()
            deleted += len(to_delete)
        print(f"Pruned {deleted} duplicate messages")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply-prune', action='store_true', help='Actually delete duplicate rows')
    args = parser.parse_args()
    db = DB()
    compact_summary(db, apply_prune=args.apply_prune)


if __name__ == '__main__':
    main()
