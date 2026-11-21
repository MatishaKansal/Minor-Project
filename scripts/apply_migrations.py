"""Apply supabase/migrations/*.sql to the database in SUPABASE_DB_URL (pure-Python driver).

Usage: python scripts/apply_migrations.py
"""

import os
import re
import ssl
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def split_statements(sql: str) -> list[str]:
    """Split on semicolons outside `$$` blocks and `--` comments."""
    sql = re.sub(r"--[^\n]*", "", sql)
    statements: list[str] = []
    current: list[str] = []
    in_dollar = False
    for piece in re.split(r"(\$\$|;)", sql):
        if piece == "$$":
            in_dollar = not in_dollar
        if piece == ";" and not in_dollar:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(piece)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def main() -> int:
    import pg8000.native

    load_dotenv(ROOT / ".env")
    parsed = urlparse(os.getenv("SUPABASE_DB_URL", "").strip())
    if not parsed.username or not parsed.password or not parsed.hostname:
        print("SUPABASE_DB_URL is empty or invalid. Set the Postgres connection string in .env.")
        return 2
    ssl_context = ssl.create_default_context()
    ca_file = os.getenv("SUPABASE_DB_CA_FILE", "").strip()
    if ca_file:
        ssl_context.load_verify_locations(cafile=ca_file)
        # Supabase's legacy database CA lacks the key-usage extension required
        # by Python 3.13+'s strict mode; certificate and hostname checks remain on.
        ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    connection = pg8000.native.Connection(
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=(parsed.path or "/postgres").lstrip("/"),
        ssl_context=ssl_context,
    )
    try:
        for path in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
            connection.run("begin")
            try:
                for statement in split_statements(path.read_text(encoding="utf-8")):
                    connection.run(statement)
                connection.run("commit")
            except Exception:
                connection.run("rollback")
                raise
            print(f"applied {path.name}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
