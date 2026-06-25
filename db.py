import psycopg2
import psycopg2.pool
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize the pool globally
db_pool = psycopg2.pool.ThreadedConnectionPool(
    1, 20, # minconn, maxconn
    host=os.getenv("DB_HOST", "localhost"),
    database=os.getenv("DB_NAME", "inventory"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    port=os.getenv("DB_PORT", 5432)
)

def query(sql, params=None, fetch=None):
    conn = db_pool.getconn() # Get a connection from the pool
    try:
        with conn: # Starts transaction
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params or ())
                if fetch == 'all':
                    return cur.fetchall()
                if fetch == 'one':
                    return cur.fetchone()
                return None
    finally:
        db_pool.putconn(conn) # Return the connection to the pool