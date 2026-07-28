import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from ticket_classifier import TicketClassifier

app = Flask(__name__)

# Initialize the local classifier
classifier = TicketClassifier()

# ==============================
# Database Setup
# ==============================

DB = "support_tickets.db"


def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_text TEXT,
        user_id TEXT,
        priority TEXT,
        category TEXT,
        reason TEXT,
        status TEXT,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


# ==============================
# Flask Routes
# ==============================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/classify", methods=["POST"])
def api():

    data = request.get_json()

    ticket_text = data.get("text", "")
    user_id = data.get("user_id", "anonymous")


    result = classifier.classify(ticket_text)


    conn = sqlite3.connect(DB)
    c = conn.cursor()


    c.execute("""
    INSERT INTO tickets(
        ticket_text,
        user_id,
        priority,
        category,
        reason,
        status,
        timestamp
    )
    VALUES (?,?,?,?,?,?,?)
    """,
    (
        ticket_text,
        user_id,
        result["priority"],
        result["category"],
        result["reason"],
        "New",
        datetime.now().isoformat()
    ))


    conn.commit()
    conn.close()


    return jsonify(result)


@app.route("/api/tickets")
def tickets():

    conn = sqlite3.connect(DB)

    conn.row_factory = sqlite3.Row


    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM tickets ORDER BY id DESC"
        )
    ]


    conn.close()


    return jsonify(rows)


# ==============================
# Start App
# ==============================

if __name__ == "__main__":
    app.run(debug=True, port=5001)