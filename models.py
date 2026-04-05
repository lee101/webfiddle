import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
import fixtures

current_dir = Path(__file__).parent
DATABASE_PATH = current_dir / "users.db"

def get_connection():
    return sqlite3.connect(DATABASE_PATH)

def init_db():
    conn = get_connection()
    with conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fiddles (
                id TEXT PRIMARY KEY,
                title TEXT,
                description TEXT,
                start_url TEXT,
                script TEXT,
                style TEXT,
                script_language INTEGER,
                style_language INTEGER,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )"""
        )
    conn.close()

@dataclass
class Fiddle:
    id: str = ""
    script: str = ""
    style: str = ""
    script_language: int = fixtures.SCRIPT_TYPES["js"]
    style_language: int = fixtures.STYLE_TYPES["css"]
    title: str = ""
    description: str = ""
    start_url: str = ""

    @classmethod
    def save(cls, obj: "Fiddle"):
        conn = get_connection()
        with conn:
            row = conn.execute("SELECT id FROM fiddles WHERE id=?", (obj.id,)).fetchone()
            if row:
                conn.execute(
                    """UPDATE fiddles SET title=?, description=?, start_url=?, script=?, style=?, script_language=?, style_language=?, updated=CURRENT_TIMESTAMP WHERE id=?""",
                    (
                        obj.title,
                        obj.description,
                        obj.start_url,
                        obj.script,
                        obj.style,
                        obj.script_language,
                        obj.style_language,
                        obj.id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO fiddles (id, title, description, start_url, script, style, script_language, style_language) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        obj.id,
                        obj.title,
                        obj.description,
                        obj.start_url,
                        obj.script,
                        obj.style,
                        obj.script_language,
                        obj.style_language,
                    ),
                )
        conn.close()

    @classmethod
    def byId(cls, fiddle_id: str) -> "Fiddle | None":
        conn = get_connection()
        row = conn.execute(
            "SELECT id, title, description, start_url, script, style, script_language, style_language FROM fiddles WHERE id=?",
            (fiddle_id,),
        ).fetchone()
        conn.close()
        if row:
            return Fiddle(
                id=row[0],
                title=row[1],
                description=row[2],
                start_url=row[3],
                script=row[4],
                style=row[5],
                script_language=row[6],
                style_language=row[7],
            )
        return None

    @classmethod
    def byUrlKey(cls, urlkey: str) -> "Fiddle | None":
        if not urlkey or urlkey.endswith("d8c4vu"):
            return default_fiddle
        pos = urlkey.rfind("-")
        if pos == -1:
            return None
        fid = urlkey[pos + 1 :]
        return cls.byId(fid)

# default fiddle
default_fiddle = Fiddle(
    id="d8c4vu",
    style="body {\n    background-color: skyblue;\n}\n",
    script=(
        "// replace the first image we see with a cat\n"
        "document.images[0].src = 'http://thecatapi.com/api/images/get?format=src&type=gif';\n\n"
        "// replace the google logo with a cat\n"
        "document.getElementById('lga').innerHTML = '<a href=\"http://thecatapi.com\"><img src=\"http://thecatapi.com/api/images/get?format=src&type=gif\"></a>';\n"
    ),
    script_language=fixtures.SCRIPT_TYPES["js"],
    style_language=fixtures.STYLE_TYPES["css"],
    title="cats",
    description="cats via the cat api",
    start_url="www.google.com",
)

init_db()
