"""Frozen entry point.

``freeze_support`` has to run before anything else imports: a bundled app
spawns its conversion workers by re-executing this same binary, and without
this line each worker would open another window instead of converting.
"""

import multiprocessing

multiprocessing.freeze_support()

from swag_converter.gui.app import main  # noqa: E402

raise SystemExit(main())
