"""Demo script: show adaptive provider probing and fallback behavior.

Usage: python -m interceptor.demo_adaptive

This script seeds the provider cache with a host marked unsupported, then
starts the probe loop with a probe function that returns True after a short
delay. It prints status before and after probing to demonstrate adaptive behavior.
"""
import time
from interceptor import provider


def demo():
    host = 'demo.provider.local'
    print('Seeding host as unsupported')
    provider.set_provider_support(host, False, ttl=5)
    print('Initial cached support:', provider.get_provider_support(host))

    start = time.time()

    # probe function: return True once 2 seconds have passed
    def probe_fn(h):
        print(f'Probing {h} at t={time.time()-start:.2f}s')
        return (time.time() - start) > 2.0

    provider.start_probe_loop(probe_fn, interval=0.5, ttl_check=0)
    try:
        # wait enough time for probe loop to run and flip support
        for i in range(6):
            print(f'[{i}] support={provider.get_provider_support(host)}')
            time.sleep(0.7)
    finally:
        provider.stop_probe_loop()
        print('Final cached support:', provider.get_provider_support(host))


if __name__ == '__main__':
    demo()
