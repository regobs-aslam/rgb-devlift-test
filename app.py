import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
import psycopg2
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, Response, jsonify, request

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
DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AWS Access Probe</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 32px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f172a; color: #e2e8f0;
  }
  h1 { margin: 0 0 6px; font-size: 26px; }
  p.sub { margin: 0 0 20px; color: #94a3b8; }
  button {
    font: inherit; cursor: pointer;
    background: #334155; color: #e2e8f0;
    border: 1px solid #475569; border-radius: 6px;
    padding: 8px 14px;
  }
  button:hover:not(:disabled) { background: #475569; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  #runAll { background: #2563eb; border-color: #2563eb; margin-bottom: 20px; }
  #runAll:hover:not(:disabled) { background: #1d4ed8; }
  .grid {
    display: grid; gap: 16px;
    grid-template-columns: repeat(2, 1fr);
  }
  @media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: #1e293b; border: 2px solid #475569; border-radius: 10px;
    padding: 18px; transition: border-color 0.2s, opacity 0.2s;
  }
  .card.running { border-color: #64748b; opacity: 0.7; }
  .card.ok      { border-color: #22c55e; }
  .card.fail    { border-color: #ef4444; }
  .card h2 { margin: 0 0 4px; font-size: 18px; }
  .card .desc { margin: 0 0 12px; color: #94a3b8; font-size: 13px; }
  .row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  .status { font-weight: 600; font-size: 14px; }
  .status.ok   { color: #22c55e; }
  .status.fail { color: #ef4444; }
  .status.run  { color: #94a3b8; }
  .ts { color: #64748b; font-size: 12px; margin-left: auto; }
  .result { display: none; }
  .result.show { display: block; }
  pre {
    background: #0f172a; border: 1px solid #334155; border-radius: 6px;
    padding: 10px; margin: 8px 0 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px; max-height: 220px; overflow: auto;
  }
</style>
</head>
<body>
  <h1>AWS Access Probe</h1>
  <p class="sub">Verifies the tenant-default IAM role's access to Secrets Manager, DynamoDB, S3, and SQS through Pod Identity.</p>
  <button id="runAll">Run all tests</button>
  <div class="grid" id="grid"></div>

<script>
  const tests = [
    { id: "secrets", name: "Secrets Manager", method: "GET",  path: "/test/secrets", desc: "Reads a secret and lists its key names." },
    { id: "dynamo",  name: "DynamoDB",        method: "POST", path: "/test/dynamo",  desc: "Puts + reads back one probe item." },
    { id: "s3",      name: "S3",              method: "POST", path: "/test/s3",      desc: "Puts + gets + deletes a probe object." },
    { id: "sqs",     name: "SQS",             method: "POST", path: "/test/sqs",     desc: "Sends + receives + deletes a probe message." },
  ];

  const grid = document.getElementById("grid");
  tests.forEach(t => {
    const el = document.createElement("div");
    el.className = "card";
    el.id = "card-" + t.id;
    el.innerHTML =
      '<h2>' + t.name + '</h2>' +
      '<p class="desc">' + t.desc + '</p>' +
      '<div class="row">' +
        '<button data-id="' + t.id + '">Run test</button>' +
        '<span class="status" id="status-' + t.id + '"></span>' +
        '<span class="ts" id="ts-' + t.id + '"></span>' +
      '</div>' +
      '<div class="result" id="result-' + t.id + '"><pre id="pre-' + t.id + '"></pre></div>';
    grid.appendChild(el);
    el.querySelector("button").addEventListener("click", () => runTest(t));
  });

  async function runTest(t) {
    const card   = document.getElementById("card-" + t.id);
    const btn    = card.querySelector("button");
    const status = document.getElementById("status-" + t.id);
    const ts     = document.getElementById("ts-" + t.id);
    const result = document.getElementById("result-" + t.id);
    const pre    = document.getElementById("pre-" + t.id);

    card.classList.remove("ok", "fail");
    card.classList.add("running");
    btn.disabled = true;
    status.className = "status run";
    status.textContent = "Running\u2026";

    let data, httpOk = false;
    try {
      const res = await fetch(t.path, { method: t.method });
      httpOk = res.ok;
      try { data = await res.json(); }
      catch (e) { data = { ok: false, error: "non-JSON response", error_type: "ParseError" }; }
    } catch (err) {
      data = { ok: false, error: String(err), error_type: "NetworkError" };
    }

    card.classList.remove("running");
    btn.disabled = false;
    ts.textContent = "Last run: " + new Date().toLocaleTimeString();
    result.classList.add("show");
    pre.textContent = JSON.stringify(data, null, 2);

    if (data && data.ok === true) {
      card.classList.add("ok");
      status.className = "status ok";
      status.textContent = "\u2713 OK";
    } else {
      card.classList.add("fail");
      status.className = "status fail";
      const et = (data && data.error_type) ? data.error_type : "Error";
      status.textContent = "\u2717 FAILED \u2014 " + et;
    }
  }

  document.getElementById("runAll").addEventListener("click", async () => {
    const btn = document.getElementById("runAll");
    btn.disabled = true;
    try { await Promise.all(tests.map(runTest)); }
    finally { btn.disabled = false; }
  });
</script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    logger.info("GET / called")
    return Response(DASHBOARD_HTML, mimetype="text/html")


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


# --- AWS Pod Identity access tests ---
# These endpoints prove that the ServiceAccount's IAM role (via EKS Pod Identity)
# actually grants the expected permissions at runtime. Secret VALUES are never
# returned or logged — only key names.


def _aws_client(service):
    return boto3.client(service, region_name=AWS_REGION)


def _error_response(exc):
    # AccessDenied / ResourceNotFound / etc. all surface as ClientError — include
    # the error_type so callers can distinguish them visually.
    err_type = type(exc).__name__
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        if code:
            err_type = code
    return (
        jsonify({"ok": False, "error": str(exc), "error_type": err_type}),
        500,
    )


@app.route("/test/secrets", methods=["GET"])
def test_secrets():
    arn = os.environ.get("SECRET_NAME_OR_ARN")
    if not arn:
        return jsonify(
            {"ok": False, "error": "SECRET_NAME_OR_ARN not set", "error_type": "ConfigError"}
        ), 500
    try:
        client = _aws_client("secretsmanager")
        # DescribeSecret proves DescribeSecret permission (and returns the ARN).
        desc = client.describe_secret(SecretId=arn)
        resolved_arn = desc.get("ARN", arn)
        # GetSecretValue proves GetSecretValue permission.
        val = client.get_secret_value(SecretId=arn)
        key_names = []
        secret_string = val.get("SecretString")
        if secret_string:
            try:
                parsed = json.loads(secret_string)
                if isinstance(parsed, dict):
                    key_names = sorted(parsed.keys())
                else:
                    key_names = ["<non-object-secret>"]
            except (json.JSONDecodeError, ValueError):
                key_names = ["<plain-string-secret>"]
        elif "SecretBinary" in val:
            key_names = ["<binary-secret>"]
        logger.info("secrets probe ok arn=%s key_count=%d", resolved_arn, len(key_names))
        return jsonify({"ok": True, "arn": resolved_arn, "key_names": key_names})
    except (ClientError, BotoCoreError, Exception) as exc:  # noqa: BLE001
        logger.warning("secrets probe failed: %s: %s", type(exc).__name__, exc)
        return _error_response(exc)


@app.route("/test/dynamo", methods=["POST"])
def test_dynamo():
    table_name = os.environ.get("DYNAMODB_TABLE")
    if not table_name:
        return jsonify(
            {"ok": False, "error": "DYNAMODB_TABLE not set", "error_type": "ConfigError"}
        ), 500
    pk_name = os.environ.get("DYNAMODB_PARTITION_KEY", "pk")
    pk_value = f"probe-{uuid.uuid4()}"
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(table_name)
        ts = int(time.time() * 1000)
        table.put_item(Item={pk_name: pk_value, "ts": ts})
        got = table.get_item(Key={pk_name: pk_value})
        if "Item" not in got:
            raise RuntimeError("put_item succeeded but get_item returned no Item")
        logger.info("dynamo probe ok table=%s pk=%s", table_name, pk_value)
        return jsonify({"ok": True, "table": table_name, "pk": pk_value})
    except (ClientError, BotoCoreError, Exception) as exc:  # noqa: BLE001
        logger.warning("dynamo probe failed: %s: %s", type(exc).__name__, exc)
        return _error_response(exc)


@app.route("/test/s3", methods=["POST"])
def test_s3():
    bucket = os.environ.get("S3_BUCKET")
    if not bucket:
        return jsonify(
            {"ok": False, "error": "S3_BUCKET not set", "error_type": "ConfigError"}
        ), 500
    key = f"probe-{uuid.uuid4()}.txt"
    body = b"hello from aslam"
    try:
        s3 = _aws_client("s3")
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        got = s3.get_object(Bucket=bucket, Key=key)
        fetched = got["Body"].read()
        if fetched != body:
            raise RuntimeError("s3 round-trip body mismatch")
        s3.delete_object(Bucket=bucket, Key=key)
        logger.info("s3 probe ok bucket=%s key=%s", bucket, key)
        return jsonify({"ok": True, "bucket": bucket, "key": key})
    except (ClientError, BotoCoreError, Exception) as exc:  # noqa: BLE001
        logger.warning("s3 probe failed: %s: %s", type(exc).__name__, exc)
        return _error_response(exc)


@app.route("/test/sqs", methods=["POST"])
def test_sqs():
    queue_url = os.environ.get("SQS_QUEUE_URL")
    if not queue_url:
        return jsonify(
            {"ok": False, "error": "SQS_QUEUE_URL not set", "error_type": "ConfigError"}
        ), 500
    probe = f"probe-{uuid.uuid4()}"
    try:
        sqs = _aws_client("sqs")
        sqs.send_message(QueueUrl=queue_url, MessageBody=probe)
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=2,
            WaitTimeSeconds=5,
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            raise RuntimeError("no message received within WaitTimeSeconds=5")
        msg = msgs[0]
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
        logger.info("sqs probe ok queue=%s", queue_url)
        return jsonify({"ok": True, "queue": queue_url, "body": msg.get("Body", "")})
    except (ClientError, BotoCoreError, Exception) as exc:  # noqa: BLE001
        logger.warning("sqs probe failed: %s: %s", type(exc).__name__, exc)
        return _error_response(exc)


if __name__ == "__main__":
    try:
        if DB_BACKEND == "dynamodb":
            init_dynamodb()
        else:
            init_postgres()
    except Exception as exc:  # noqa: BLE001
        # The /save and /requests endpoints need the DB, but the probe
        # dashboard (/ and /test/*) does not. Don't crashloop the pod over
        # a DB that may be unreachable or intentionally unconfigured.
        logger.warning(
            "DB init failed (backend=%s): %s: %s — /save and /requests will return 500 until fixed",
            DB_BACKEND, type(exc).__name__, exc,
        )
    logger.info("Starting server on port 5000 (backend=%s)", DB_BACKEND)
    app.run(host="0.0.0.0", port=5000)
