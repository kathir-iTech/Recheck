"""SQLite state for a simulated packaging station."""

from contextlib import closing
import sqlite3


def read_events(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        rows = database.execute("SELECT command_id, advance FROM advances ORDER BY advance").fetchall()
    return [{"command_id": command, "advance": advance} for command, advance in rows]


class StationStore:
    def __init__(self, path, durable_ids):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS advances (
                advance INTEGER PRIMARY KEY,
                command_id INTEGER NOT NULL
            )
        """)
        # The comparison keeps simulated motion in both cases. Only the command history differs.
        self.history = "main.completed" if durable_ids else "temp.completed"
        self.connection.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.history} (
                command_id INTEGER PRIMARY KEY,
                amount INTEGER NOT NULL
            )
        """)

    def count(self):
        return self.connection.execute("SELECT COUNT(*) FROM advances").fetchone()[0]

    def apply(self, command_id, amount, protected):
        try:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                total = self.count()
                if protected:
                    prior = self.connection.execute(
                        f"SELECT amount FROM {self.history} WHERE command_id = ?", (command_id,)
                    ).fetchone()
                    if prior:
                        return ("ok" if prior[0] == amount else "invalid"), total
                if total + amount > 65535:
                    return "invalid", total
                self.connection.executemany(
                    "INSERT INTO advances (advance, command_id) VALUES (?, ?)",
                    ((index, command_id) for index in range(total + 1, total + amount + 1)),
                )
                if protected:
                    self.connection.execute(
                        f"INSERT INTO {self.history} (command_id, amount) VALUES (?, ?)", (command_id, amount)
                    )
            return "ok", total + amount
        except sqlite3.Error:
            return "storage_failure", None

    def close(self):
        self.connection.close()
