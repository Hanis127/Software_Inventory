import psycopg2
import psycopg2.pool
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize the pool globally
db_pool = psycopg2.pool.ThreadedConnectionPool(
    1, 80, # minconn, maxconn
    host=os.getenv("DB_HOST", "localhost"),
    database=os.getenv("DB_NAME", "inventory"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    port=os.getenv("DB_PORT", 5432)
)

def query(sql, params=None, fetch=None):
    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            result = None
            if fetch == 'all':
                result = cur.fetchall()
            elif fetch == 'one':
                result = cur.fetchone()
            conn.commit()
            return result
    except Exception as e:
        if conn: conn.rollback()
        raise e # Re-raise to log the actual error
    finally:
        if conn:
            db_pool.putconn(conn) # MANDATORY: Return the connection