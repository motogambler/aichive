import importlib


def test_summarize_group_returns_string():
    cw = importlib.import_module('interceptor.compaction_worker')
    parts = [
        'This is a short message about an event. It has useful info.',
        'Another message that overlaps in content and should be merged.'
    ]
    s = cw.summarize_group(parts)
    assert isinstance(s, str)
    assert len(s) > 0


def test_compaction_worker_imports():
    cw = importlib.import_module('interceptor.compaction_worker')
    assert hasattr(cw, 'run_once')
