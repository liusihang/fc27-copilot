import re
import sqlite3
from pathlib import Path

from .errors import FC27Error
from .schema import RUNTIME_SCHEMA, RUNTIME_SCHEMA_VERSION


PERSONA_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class RuntimeDB:
    def __init__(self, path, persona_id):
        self.path = Path(path)
        self.persona_id = str(persona_id)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self, identity):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        with self.connect() as connection:
            if is_new:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(RUNTIME_SCHEMA)
                connection.execute(
                    "INSERT INTO runtime_meta(key, value) VALUES ('schema_version', ?)",
                    (RUNTIME_SCHEMA_VERSION,),
                )
            self._validate_schema(connection)
            rows = connection.execute("SELECT persona_id FROM account_state").fetchall()
            if rows and any(str(row[0]) != self.persona_id for row in rows):
                raise FC27Error(
                    "ACCOUNT_MISMATCH",
                    f"Runtime database belongs to Persona {rows[0][0]}, not {self.persona_id}.",
                    recovery="Select the runtime database directory matching the logged-in Persona.",
                )
            connection.execute(
                """INSERT INTO account_state(persona_id, club_id, club_name, platform)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(persona_id) DO UPDATE SET
                     club_id = excluded.club_id,
                     club_name = excluded.club_name,
                     platform = excluded.platform""",
                (
                    self.persona_id,
                    identity.get("club_id"),
                    identity.get("club_name"),
                    identity["platform"],
                ),
            )
            connection.commit()
        return self.account_summary()

    def account_summary(self):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM account_state WHERE persona_id = ?", (self.persona_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["runtime_path"] = str(self.path)
        return result

    def _validate_schema(self, connection):
        try:
            version = connection.execute(
                "SELECT value FROM runtime_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError as error:
            raise FC27Error(
                "RUNTIME_SCHEMA_INVALID",
                f"Runtime database schema is missing required tables: {error}",
                recovery="Move the invalid database aside and create a new Persona runtime database.",
            ) from error
        if version is None or version[0] != RUNTIME_SCHEMA_VERSION:
            raise FC27Error(
                "RUNTIME_SCHEMA_INVALID",
                f"Expected runtime schema {RUNTIME_SCHEMA_VERSION}, found {version[0] if version else 'missing'}.",
                recovery="Use a runtime database created by the current fc27d version.",
            )


class RuntimeManager:
    def __init__(self, accounts_root):
        self.accounts_root = Path(accounts_root)
        self.active = None

    def activate(self, identity):
        persona_id = str(identity.get("persona_id") or "")
        if not persona_id or not PERSONA_ID_PATTERN.fullmatch(persona_id):
            raise FC27Error(
                "INVALID_PERSONA_ID",
                "EA Persona ID is empty or contains unsupported path characters.",
                recovery="Re-read identity from the authenticated FC27 Web App session.",
            )
        platform = str(identity.get("platform") or "").lower()
        if platform not in ("pc", "ps5"):
            raise FC27Error(
                "INVALID_PLATFORM",
                f"Unsupported FC27 platform: {platform or 'missing'}.",
                recovery="Re-read identity from the selected EA Persona.",
            )
        normalized = {**identity, "persona_id": persona_id, "platform": platform}
        runtime = RuntimeDB(self.accounts_root / persona_id / "runtime.sqlite", persona_id)
        summary = runtime.initialize(normalized)
        self.active = runtime
        return summary

    def status(self):
        return self.active.account_summary() if self.active else None
