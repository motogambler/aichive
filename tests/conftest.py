"""Test conftest to ensure repository root is on sys.path for imports.

This helps pytest locate the `interceptor` package when running tests
from various working directories or environments.
"""
import os
import sys

root = os.path.dirname(os.path.dirname(__file__))
if root not in sys.path:
    sys.path.insert(0, root)
