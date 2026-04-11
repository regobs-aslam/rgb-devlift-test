import logging
import os
from datetime import datetime, timezone

import psycopg2
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def get_db_connection():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        database=os.environ["POSTGRES_DATABASE"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS request (
                    id SERIAL PRIMARY KEY,
                    datetime TIMESTAMPTZ NOT NULL,
                    remarks TEXT
                )
            """)
        conn.commit()
        logger.info("Table 'request' is ready")
    finally:
        conn.close()


@app.route("/")
def hello():
    logger.info("GET / called")
    return jsonify({"message": "Hello, World!"})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/save")
def save():
    remarks = request.args.get("remarks", "")
    now = datetime.now(timezone.utc)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO request (datetime, remarks) VALUES (%s, %s) RETURNING id",
                (now, remarks),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        logger.info("Saved request id=%s remarks=%s", row_id, remarks)
        return jsonify({"id": row_id, "datetime": now.isoformat(), "remarks": remarks})
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    logger.info("Starting server on port 5000")
    app.run(host="0.0.0.0", port=5000)
