#!/usr/bin/env python
"""Bootstrap launcher that works even for embedded/portable Python builds.

    python run.py presets
    python run.py simulate --preset ar_broadband
    python run.py serve

After ``pip install -e .`` you can also use ``python -m thinfilmforge`` or the
``thinfilm`` console script directly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from thinfilmforge.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
