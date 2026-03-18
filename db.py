"""
Database abstraction layer - SQLite (local) / PostgreSQL (Railway)
DATABASE_URL環境変数があればPostgreSQL、なければSQLiteを使用
"""
import os
import sqlite3
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

DB_PATH = Path(__file__).parent / "users.db"


def get_db():
    """DB接続を取得（SQLite or PostgreSQL）"""
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return PgConnection(conn)
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return SqliteConnection(conn)


class SqliteConnection:
    """SQLite wrapper"""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        if params:
            return self._conn.execute(sql, params)
        return self._conn.execute(sql)

    def fetchone(self, sql, params=None):
        cur = self.execute(sql, params)
        return cur.fetchone()

    def fetchall(self, sql, params=None):
        cur = self.execute(sql, params)
        return cur.fetchall()

    def insert_returning_id(self, sql, params=None):
        """INSERT後にlast_insert_rowidを返す"""
        self.execute(sql, params)
        return self.fetchone("SELECT last_insert_rowid()")[0]

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class PgConnection:
    """PostgreSQL wrapper"""
    def __init__(self, conn):
        self._conn = conn

    def _convert_sql(self, sql):
        """SQLiteの?プレースホルダーを%sに変換"""
        return sql.replace("?", "%s")

    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        sql = self._convert_sql(sql)
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return cur

    def fetchone(self, sql, params=None):
        cur = self.execute(sql, params)
        return cur.fetchone()

    def fetchall(self, sql, params=None):
        cur = self.execute(sql, params)
        return cur.fetchall()

    def insert_returning_id(self, sql, params=None):
        """INSERT後にIDを返す（RETURNING id付きで実行）"""
        sql_pg = self._convert_sql(sql)
        if "RETURNING" not in sql_pg.upper():
            sql_pg = sql_pg.rstrip().rstrip(";") + " RETURNING id"
        cur = self._conn.cursor()
        if params:
            cur.execute(sql_pg, params)
        else:
            cur.execute(sql_pg)
        row = cur.fetchone()
        return row[0] if row else None

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def init_db():
    """テーブル作成 + 初期管理者"""
    from werkzeug.security import generate_password_hash

    if DATABASE_URL:
        _init_pg()
    else:
        _init_sqlite()


def _init_sqlite():
    from werkzeug.security import generate_password_hash

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            must_change_password INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            token TEXT NOT NULL,
            basic_id TEXT DEFAULT '',
            max_friends INTEGER DEFAULT 500,
            friend_count INTEGER DEFAULT 0,
            channel_secret TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            account_ids TEXT NOT NULL,
            message TEXT NOT NULL,
            mode TEXT DEFAULT 'broadcast',
            user_ids TEXT DEFAULT '[]',
            scheduled_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            image_url TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS line_friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            picture_url TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            UNIQUE(account_id, line_user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            message_text TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            media_url TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_lookup
            ON chat_messages(account_id, line_user_id, created_at)
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_read_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            last_read_id INTEGER DEFAULT 0,
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            UNIQUE(account_id, line_user_id)
        )
    """)

    # ステップ配信テーブル
    c.execute("""
        CREATE TABLE IF NOT EXISTS step_scenarios (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            auto_start INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS step_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_id TEXT NOT NULL,
            step_number INTEGER NOT NULL,
            delay_minutes INTEGER NOT NULL DEFAULT 0,
            message_text TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scenario_id) REFERENCES step_scenarios(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS step_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            current_step INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (scenario_id) REFERENCES step_scenarios(id),
            UNIQUE(scenario_id, account_id, line_user_id)
        )
    """)

    # 既存DB移行用のALTER TABLE
    for stmt in [
        "ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0",
        "ALTER TABLE chat_messages ADD COLUMN message_type TEXT DEFAULT 'text'",
        "ALTER TABLE chat_messages ADD COLUMN media_url TEXT DEFAULT ''",
        "ALTER TABLE schedules ADD COLUMN image_url TEXT DEFAULT ''",
        "ALTER TABLE accounts ADD COLUMN api_status TEXT DEFAULT 'active'",
        "ALTER TABLE accounts ADD COLUMN greeting_message TEXT DEFAULT ''",
        "ALTER TABLE accounts ADD COLUMN greeting_image_url TEXT DEFAULT ''",
    ]:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass

    # 初期管理者
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO users (email, password_hash, is_admin, must_change_password) VALUES (?, ?, 1, 1)",
            ("admin@admin", generate_password_hash("admin")),
        )
        print("[INFO] 初期管理者アカウントを作成しました (admin@admin / admin)")

    conn.commit()
    conn.close()


def _init_pg():
    from werkzeug.security import generate_password_hash

    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            must_change_password INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            token TEXT NOT NULL,
            basic_id TEXT DEFAULT '',
            max_friends INTEGER DEFAULT 500,
            friend_count INTEGER DEFAULT 0,
            channel_secret TEXT DEFAULT '',
            api_status TEXT DEFAULT 'active',
            greeting_message TEXT DEFAULT '',
            greeting_image_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # PG既存DB移行用
    for stmt in [
        "ALTER TABLE accounts ADD COLUMN api_status TEXT DEFAULT 'active'",
        "ALTER TABLE accounts ADD COLUMN greeting_message TEXT DEFAULT ''",
        "ALTER TABLE accounts ADD COLUMN greeting_image_url TEXT DEFAULT ''",
    ]:
        try:
            c.execute(stmt)
        except Exception:
            conn.rollback()

    c.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            account_ids TEXT NOT NULL,
            message TEXT NOT NULL,
            mode TEXT DEFAULT 'broadcast',
            user_ids TEXT DEFAULT '[]',
            scheduled_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            image_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS line_friends (
            id SERIAL PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            line_user_id TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            picture_url TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, line_user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            line_user_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            message_text TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            media_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_lookup
            ON chat_messages(account_id, line_user_id, created_at)
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_read_status (
            id SERIAL PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            line_user_id TEXT NOT NULL,
            last_read_id INTEGER DEFAULT 0,
            UNIQUE(account_id, line_user_id)
        )
    """)

    # ステップ配信テーブル
    c.execute("""
        CREATE TABLE IF NOT EXISTS step_scenarios (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL REFERENCES accounts(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            auto_start INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS step_messages (
            id SERIAL PRIMARY KEY,
            scenario_id TEXT NOT NULL REFERENCES step_scenarios(id),
            step_number INTEGER NOT NULL,
            delay_minutes INTEGER NOT NULL DEFAULT 0,
            message_text TEXT DEFAULT '',
            image_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS step_subscriptions (
            id SERIAL PRIMARY KEY,
            scenario_id TEXT NOT NULL REFERENCES step_scenarios(id),
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            current_step INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scenario_id, account_id, line_user_id)
        )
    """)

    # 初期管理者
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO users (email, password_hash, is_admin, must_change_password) VALUES (%s, %s, 1, 1)",
            ("admin@admin", generate_password_hash("admin")),
        )
        print("[INFO] 初期管理者アカウントを作成しました (admin@admin / admin)")

    conn.commit()
    conn.close()
