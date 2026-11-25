import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("WEBFIDDLE_DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "users.db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
