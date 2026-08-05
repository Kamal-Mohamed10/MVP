#!/usr/bin/env python3
import argparse
import os
import sqlite3
import sys
from sqlalchemy import create_engine, text

TICKET_COLUMNS = [
    "id",
    "ticket_text",
    "user_id",
    "channel",
    "priority",
    "category",
    "reason",
    "status",
    "timestamp",
    "eta",
    "resolved_at",
    "resolution_notes",
    "csat_score",
    "escalated",
    "escalation_reason",
    "corrected_category",
    "corrected_priority",
    "corrected_request_type",
    "full_result",
]

FEEDBACK_COLUMNS = [
    "id",
    "ticket_text",
    "original_category",
    "corrected_category",
    "original_priority",
    "corrected_priority",
    "corrected_request_type",
    "created_at",
]


def create_sqlite_rows(sqlite_path, query):
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(query).fetchall()]
    finally:
        conn.close()
    return rows


def create_postgres_engine(destination_url):
    return create_engine(destination_url)


def ensure_postgres_schema(engine):
    ticket_table = """
    CREATE TABLE IF NOT EXISTS tickets (
        id SERIAL PRIMARY KEY,
        ticket_text TEXT,
        user_id TEXT,
        channel TEXT,
        priority TEXT,
        category TEXT,
        reason TEXT,
        status TEXT,
        timestamp TEXT,
        eta TEXT,
        resolved_at TEXT,
        resolution_notes TEXT,
        csat_score INTEGER,
        escalated INTEGER DEFAULT 0,
        escalation_reason TEXT,
        corrected_category TEXT,
        corrected_priority TEXT,
        corrected_request_type TEXT,
        full_result TEXT
    )
    """

    feedback_table = """
    CREATE TABLE IF NOT EXISTS feedback (
        id SERIAL PRIMARY KEY,
        ticket_text TEXT,
        original_category TEXT,
        corrected_category TEXT,
        original_priority TEXT,
        corrected_priority TEXT,
        corrected_request_type TEXT,
        created_at TEXT
    )
    """

    with engine.begin() as conn:
        conn.execute(text(ticket_table))
        conn.execute(text(feedback_table))


def truncate_postgres_tables(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE feedback, tickets RESTART IDENTITY CASCADE"))


def insert_rows(engine, table_name, columns, rows, ignore_conflicts=False):
    if not rows:
        return 0

    placeholders = ", ".join(f":{col}" for col in columns)
    columns_sql = ", ".join(columns)
    insert_sql = f"INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders})"
    if ignore_conflicts:
        insert_sql += " ON CONFLICT DO NOTHING"

    inserted = 0
    with engine.begin() as conn:
        for row in rows:
            try:
                conn.execute(text(insert_sql), row)
                inserted += 1
            except Exception as exc:
                print(f"Warning: failed to insert row into {table_name}: {exc}")
    return inserted


def reset_postgres_sequence(engine, table_name):
    with engine.begin() as conn:
        try:
            sequence_sql = text(
                "SELECT setval(pg_get_serial_sequence(:table, 'id'), COALESCE(MAX(id), 1), true) FROM " + table_name
            )
            conn.execute(sequence_sql, {"table": table_name})
        except Exception as exc:
            print(f"Warning: failed to reset sequence for {table_name}: {exc}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Migrate support ticket data from SQLite to Postgres."
    )
    parser.add_argument(
        "--sqlite-path",
        required=True,
        help="Path to the existing SQLite database file (support_tickets.db).",
    )
    parser.add_argument(
        "--destination-url",
        help="Postgres DATABASE_URL destination. If omitted, the script uses the DATABASE_URL environment variable.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Truncate destination tables before migrating data.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    sqlite_path = args.sqlite_path
    destination_url = args.destination_url or os.environ.get("DATABASE_URL")

    if not destination_url:
        print("Error: destination URL must be provided via --destination-url or DATABASE_URL environment variable.")
        sys.exit(1)

    if not os.path.exists(sqlite_path):
        print(f"Error: SQLite path does not exist: {sqlite_path}")
        sys.exit(1)

    print(f"Migrating data from SQLite: {sqlite_path}")
    print(f"Destination Postgres: {destination_url}")

    tickets = create_sqlite_rows(sqlite_path, "SELECT * FROM tickets ORDER BY id ASC")
    feedback = create_sqlite_rows(sqlite_path, "SELECT * FROM feedback ORDER BY id ASC")

    engine = create_postgres_engine(destination_url)
    if engine.dialect.name != "postgresql":
        print("Error: destination URL must point to a Postgres database.")
        sys.exit(1)

    ensure_postgres_schema(engine)

    if args.overwrite:
        print("Truncating destination tables...")
        truncate_postgres_tables(engine)

    ticket_count = insert_rows(engine, "tickets", TICKET_COLUMNS, tickets, ignore_conflicts=True)
    feedback_count = insert_rows(engine, "feedback", FEEDBACK_COLUMNS, feedback, ignore_conflicts=True)

    if engine.dialect.name == "postgresql":
        reset_postgres_sequence(engine, "tickets")
        reset_postgres_sequence(engine, "feedback")

    print(f"Migration complete: {ticket_count} tickets and {feedback_count} feedback rows copied.")
    if len(tickets) != ticket_count:
        print(f"Note: {len(tickets) - ticket_count} ticket rows were skipped due to conflicts.")
    if len(feedback) != feedback_count:
        print(f"Note: {len(feedback) - feedback_count} feedback rows were skipped due to conflicts.")


if __name__ == "__main__":
    main()
