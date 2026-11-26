"""``alox.exe`` (console): the integrated application, as the ``alox`` command of ``pip install`` (``alox.app:main``).

The server / headless application by default; ``--check``, ``--ui``, ``--simulate`` and the other options as
``alox --help`` lists them.
"""

import multiprocessing
import sys

from alox.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
