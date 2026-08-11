import traceback

try:
    from app import app
except Exception:
    error_message = traceback.format_exc()
    from flask import Flask

    app = Flask(__name__)

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def deployment_error(path):
        return (
            "<h1>App import failed on Vercel</h1>"
            "<pre style='white-space: pre-wrap; font-family: monospace;'>"
            + error_message.replace("<", "&lt;").replace(">", "&gt;")
            + "</pre>"
        ), 500
