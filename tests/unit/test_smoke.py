import sys

import taintwall


def test_python_is_312() -> None:
    assert sys.version_info[:2] == (3, 12)


def test_taintwall_imports() -> None:
    assert isinstance(taintwall.__version__, str)
