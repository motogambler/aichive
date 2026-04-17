from interceptor.code_compress import compress_python_source


def test_compress_python_remove_comments():
    src = """
def foo(x):
    # this is a comment
    return x  # trailing comment

"""
    out = compress_python_source(src)
    assert 'comment' not in out
    assert 'def foo' in out
