import os
import time
_orig_remove = os.remove

def _remove_with_retry(path):
    # Retry on Windows PermissionError which can occur when files are
    # closed by other OS subsystems. Perform multiple retries to reduce
    # flaky test failures in CI/dev environments.
    for _ in range(20):
        try:
            return _orig_remove(path)
        except PermissionError:
            time.sleep(0.1)
    return _orig_remove(path)

os.remove = _remove_with_retry
