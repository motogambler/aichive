"""Parquet export stub.

Uses `pyarrow` if available. Falls back to JSONL export when PyArrow
is not installed.
"""
from interceptor.db import DB


def export_parquet(path: str = 'export.parquet') -> str:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception:
        # fallback to JSONL
        from .export import export_to_jsonl
        p = export_to_jsonl(path.replace('.parquet', '.jsonl'))
        return p

    db = DB()
    rows = db.get_all_messages()
    ids = []
    directions = []
    urls = []
    methods = []
    tss = []
    contents = []
    metas = []
    for r in rows:
        mid = r[0]
        ids.append(mid)
        directions.append(r[1])
        urls.append(r[2])
        methods.append(r[3])
        tss.append(r[4])
        try:
            content = db.get_message_content(mid).decode('utf-8', errors='replace')
        except Exception:
            content = ''
        contents.append(content)
        metas.append(r[8] if len(r) > 8 else '{}')

    table = pa.table({
        'id': ids,
        'direction': directions,
        'url': urls,
        'method': methods,
        'ts': tss,
        'content': contents,
        'metadata': metas,
    })
    pq.write_table(table, path)
    return path


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=str, default='export.parquet')
    args = parser.parse_args()
    print(export_parquet(args.out))


if __name__ == '__main__':
    main()
