"""CI-only probe for the desktop's actual database and Qt settings paths."""

import sqlite3
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings

sys.path.insert(0, "/app")
from protocol_backend import ProtocolBackend


database = ProtocolBackend._application_dir() / "pet_results.db"
assert database.resolve() == Path("/data/pet_results.db")
settings = QSettings("MoleRespec", "MoleRespec")
assert Path(settings.fileName()).is_relative_to("/data/config")
with sqlite3.connect(database) as connection:
    if sys.argv[1] == "write":
        connection.execute("CREATE TABLE deployment_smoke (value TEXT)")
        connection.execute("INSERT INTO deployment_smoke VALUES (?)", ("persisted",))
        settings.setValue("deployment/smoke", "persisted")
        settings.sync()
        assert settings.status() == QSettings.Status.NoError
    else:
        assert connection.execute("SELECT value FROM deployment_smoke").fetchone() == ("persisted",)
        assert settings.value("deployment/smoke") == "persisted"
