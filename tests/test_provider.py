import time
from interceptor import provider


def test_set_and_get_provider_support():
    host = 'example.com'
    # ensure fresh
    provider.set_provider_support(host, True, ttl=1)
    val = provider.get_provider_support(host)
    assert val is True
    # wait for expiry
    time.sleep(1.1)
    val2 = provider.get_provider_support(host)
    assert val2 is None


def test_probe_loop_updates_cache():
    host = 'probe-host.local'
    # set as unsupported with short ttl
    provider.set_provider_support(host, False, ttl=1)

    called = {'count': 0}

    def fake_probe(h):
        called['count'] += 1
        # return True to indicate host now supports encoding
        return True

    # start probe loop with small interval and ttl_check=0 so it probes immediately
    provider.start_probe_loop(fake_probe, interval=0.2, ttl_check=0)
    try:
        # wait for probe to run
        time.sleep(0.5)
        val = provider.get_provider_support(host)
        # probe should have updated cache to True
        assert val is True
        assert called['count'] >= 1
    finally:
        provider.stop_probe_loop()
