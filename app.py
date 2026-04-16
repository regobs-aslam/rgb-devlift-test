import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
import psycopg2
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Configuration ---
DB_BACKEND = os.environ.get("DB_BACKEND", "postgres")  # "postgres" or "dynamodb"

# Postgres (optional, defaults provided)
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DATABASE = os.environ.get("POSTGRES_DATABASE", "postgres")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")

# DynamoDB (optional, defaults provided)
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "request")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_ENDPOINT_URL = os.environ.get("DYNAMODB_ENDPOINT_URL")  # for local testing


# --- Postgres ---
def get_db_connection():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DATABASE,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def init_postgres():
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
        logger.info("Postgres table 'request' is ready")
    finally:
        conn.close()


def save_postgres(remarks):
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
        return {"id": row_id, "datetime": now.isoformat(), "remarks": remarks}
    finally:
        conn.close()


def get_requests_postgres():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, datetime, remarks FROM request ORDER BY id DESC")
            rows = cur.fetchall()
        return [
            {"id": r[0], "datetime": r[1].isoformat(), "remarks": r[2]} for r in rows
        ]
    finally:
        conn.close()


# --- DynamoDB ---
def get_dynamodb_table():
    kwargs = {"region_name": AWS_REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    dynamodb = boto3.resource("dynamodb", **kwargs)
    return dynamodb.Table(DYNAMODB_TABLE_NAME)


def get_dynamodb_client():
    kwargs = {"region_name": AWS_REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
    return boto3.client("dynamodb", **kwargs)


def init_dynamodb():
    client = get_dynamodb_client()
    try:
        client.describe_table(TableName=DYNAMODB_TABLE_NAME)
        logger.info("DynamoDB table '%s' already exists", DYNAMODB_TABLE_NAME)
    except client.exceptions.ResourceNotFoundException:
        client.create_table(
            TableName=DYNAMODB_TABLE_NAME,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=DYNAMODB_TABLE_NAME)
        logger.info("DynamoDB table '%s' created", DYNAMODB_TABLE_NAME)


def save_dynamodb(remarks):
    now = datetime.now(timezone.utc)
    item_id = str(uuid.uuid4())
    table = get_dynamodb_table()
    table.put_item(
        Item={
            "id": item_id,
            "datetime": now.isoformat(),
            "remarks": remarks,
        }
    )
    logger.info("Saved DynamoDB item id=%s remarks=%s", item_id, remarks)
    return {"id": item_id, "datetime": now.isoformat(), "remarks": remarks}


def get_requests_dynamodb():
    table = get_dynamodb_table()
    response = table.scan()
    items = response.get("Items", [])
    # handle pagination
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    # sort by datetime descending
    items.sort(key=lambda x: x.get("datetime", ""), reverse=True)
    return [
        {"id": item["id"], "datetime": item["datetime"], "remarks": item.get("remarks", "")}
        for item in items
    ]


# --- Routes ---
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
    if DB_BACKEND == "dynamodb":
        result = save_dynamodb(remarks)
    else:
        result = save_postgres(remarks)
    return jsonify(result)


@app.route("/requests")
def get_requests():
    if DB_BACKEND == "dynamodb":
        results = get_requests_dynamodb()
    else:
        results = get_requests_postgres()
    return jsonify(results)


if __name__ == "__main__":
    if DB_BACKEND == "dynamodb":
        init_dynamodb()
    else:
        init_postgres()
    logger.info("Starting server on port 5000 (backend=%s)", DB_BACKEND)
    app.run(host="0.0.0.0", port=5000)
