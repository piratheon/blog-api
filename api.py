"""WSGI entrypoint.

Vercel imports this module and looks for a callable named ``app``. The whole
repository is bundled into the function, so the ``fieldnotes`` package travels
with it.

Configuration is read from environment variables; see README.md.
"""

import os

from fieldnotes import create_app
from fieldnotes.config import Config

# Vercel's Python runtime can import a package before it is on sys.path when the
# entrypoint sits at the repository root, so make the import explicit.
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A serverless instance has an ephemeral filesystem and no shell to run
# migrations in, so the schema is created on first import when asked to.
os.environ.setdefault("AUTO_CREATE_SCHEMA", "1")

app = create_app(Config())