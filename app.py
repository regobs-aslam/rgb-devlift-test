import logging

from flask import Flask, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def hello():
    logger.info("GET / called")
    return jsonify({"message": "Hello, World!"})


if __name__ == "__main__":
    logger.info("Starting server on port 5000")
    app.run(host="0.0.0.0", port=5000)
