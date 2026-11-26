import os
import ssl
from urllib.parse import unquote, urlparse
from pathlib import Path
from dotenv import load_dotenv
import pg8000.native

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

parsed = urlparse(os.getenv("SUPABASE_DB_URL", "").strip())
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

conn = pg8000.native.Connection(
    user=unquote(parsed.username),
    password=unquote(parsed.password),
    host=parsed.hostname,
    port=parsed.port or 5432,
    database=(parsed.path or "/postgres").lstrip("/"),
    ssl_context=ssl_context,
)

create_sql = [
    """
    CREATE TABLE IF NOT EXISTS chat_notebooks (
        notebook_id TEXT PRIMARY KEY,
        business_id UUID REFERENCES business(business_id) ON DELETE CASCADE,
        user_id UUID,
        title TEXT NOT NULL,
        messages JSONB NOT NULL DEFAULT '[]'::jsonb,
        message_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_notebooks_business ON chat_notebooks(business_id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_notebooks_user ON chat_notebooks(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_notebooks_updated ON chat_notebooks(updated_at DESC)",
    "ALTER TABLE chat_notebooks ENABLE ROW LEVEL SECURITY",
    "GRANT ALL ON chat_notebooks TO postgres, service_role, authenticated, anon",
    "NOTIFY pgrst, 'reload schema'",
]

for stmt in create_sql:
    conn.run(stmt.strip())

conn.run("commit")
print("chat_notebooks table created successfully!")
conn.close()
