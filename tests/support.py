"""Shared test helpers.

The program is a script called `iv-suggest`, with no .py suffix, so it cannot be
imported by name. Load it by path instead; importing it is side effect free
(module level does no database work).
"""

import importlib.machinery
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "iv-suggest")


def load(**env):
    """A fresh copy of the module, with `env` set while it initialises.

    Settings are read at import time, so IV_SUGGEST_ACCOUNT and friends have to
    be in place before the module body runs.
    """
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    # An empty ENVFILE keeps the developer's own /etc/iv-suggest/env out of it.
    saved["IV_SUGGEST_ENVFILE"] = os.environ.get("IV_SUGGEST_ENVFILE")
    os.environ["IV_SUGGEST_ENVFILE"] = os.path.join(ROOT, "tests", "empty.env")
    try:
        # No .py suffix, so the loader has to be named explicitly.
        loader = importlib.machinery.SourceFileLoader("iv_suggest", SCRIPT)
        spec = importlib.util.spec_from_loader("iv_suggest", loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def source():
    with open(SCRIPT) as fh:
        return fh.read()


if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
