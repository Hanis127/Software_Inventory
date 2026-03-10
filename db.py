import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "inventory"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT", 5432)
    )

def query(sql, params=None, fetch=None):
    """
    fetch=None  → execute only (INSERT/UPDATE/DELETE)
    fetch='one' → fetchone()
    fetch='all' → fetchall()
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            if fetch == 'all':
                return cur.fetchall()
            if fetch == 'one':
                return cur.fetchone()