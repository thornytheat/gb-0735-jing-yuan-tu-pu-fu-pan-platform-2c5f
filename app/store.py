import json
import sqlite3
from contextlib import closing, contextmanager

from app.errors import ReviewError
from app.manifest import canonical


class RunStore:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS wafer_runs (
                    run_id TEXT PRIMARY KEY,
                    manifest TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    pinned TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS wafer_rulings (
                    run_id TEXT NOT NULL REFERENCES wafer_runs(run_id),
                    wafer TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    row_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    bin INTEGER NOT NULL,
                    reviewer TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, wafer, x, y)
                )
            """)

    def put(self, manifest, snapshot, pinned):
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO wafer_runs VALUES (?, ?, ?, ?) ON CONFLICT(run_id) DO NOTHING",
                (manifest["run_id"], canonical(manifest), canonical(snapshot), canonical(pinned)),
            )
            created = cursor.rowcount == 1
            row = connection.execute("SELECT manifest FROM wafer_runs WHERE run_id = ?",
                                     (manifest["run_id"],)).fetchone()
        return json.loads(row[0]), created

    def get(self, run_id):
        with self.connect() as connection:
            row = connection.execute("SELECT manifest, snapshot, pinned FROM wafer_runs WHERE run_id = ?",
                                     (run_id,)).fetchone()
        if row is None:
            raise ReviewError("RUN_NOT_FOUND", "图批未找到", 404)
        return tuple(json.loads(part) for part in row)

    def list(self):
        with self.connect() as connection:
            rows = connection.execute("SELECT manifest FROM wafer_runs ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    RULING_COLUMNS = ("wafer", "x", "y", "row_id", "attempt", "result", "bin",
                      "reviewer", "note", "created_at")

    def put_ruling(self, run_id, ruling):
        values = tuple(ruling[column] for column in self.RULING_COLUMNS)
        with self.connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO wafer_rulings VALUES (?, {', '.join('?' * len(values))}) "
                "ON CONFLICT(run_id, wafer, x, y) DO NOTHING",
                (run_id, *values),
            )
            created = cursor.rowcount == 1
        return self.find_ruling(run_id, ruling["wafer"], ruling["x"], ruling["y"]), created

    def find_ruling(self, run_id, wafer, x, y):
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT {', '.join(self.RULING_COLUMNS)} FROM wafer_rulings "
                "WHERE run_id = ? AND wafer = ? AND x = ? AND y = ?",
                (run_id, wafer, x, y),
            ).fetchone()
        return dict(zip(self.RULING_COLUMNS, row)) if row else None

    def rulings(self, run_id):
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT {', '.join(self.RULING_COLUMNS)} FROM wafer_rulings "
                "WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        return [dict(zip(self.RULING_COLUMNS, row)) for row in rows]
