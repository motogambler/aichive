from interceptor.redact import redact_text


def test_redact_email():
    s = 'contact me at alice@example.com for details'
    r = redact_text(s)
    assert '<REDACTED-EMAIL>' in r


def test_redact_api_key():
    s = 'openai key: sk-1234567890abcdef123456'
    r = redact_text(s)
    assert '<REDACTED-API-KEY>' in r
