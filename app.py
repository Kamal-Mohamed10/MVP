import os
import re
import json
import csv
import sqlite3
import tempfile
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from ticket_classifier import TicketClassifier

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
    static_url_path="/static"
)
app.config["JSON_SORT_KEYS"] = False

# Initialize the local classifier
classifier = TicketClassifier()

# ==============================
# Database Setup
# ==============================

# Use a stable project-local DB so imported CSV data persists across restarts.
# On Vercel the serverless filesystem is read-only except /tmp, and each
# instance has its own ephemeral storage, so we point the DB at /tmp there
# and re-seed automatically on each cold start.
# Set SUPPORT_TICKETS_DB to override the path explicitly.
if os.environ.get("VERCEL"):
    DB = os.path.join(tempfile.gettempdir(), "support_tickets.db")
else:
    DB = os.environ.get("SUPPORT_TICKETS_DB") or os.path.join(BASE_DIR, "support_tickets.db")
DB = os.path.abspath(DB)

CHANNEL_CHOICES = ["email", "chat", "phone", "other"]


def init_db():
    try:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
    except OSError as e:
        print(f"Could not create DB directory {os.path.dirname(DB)}: {e}")

    try:
        conn = sqlite3.connect(DB, timeout=10)
        c = conn.cursor()

        # Main tickets table - store everything needed for display, dates, and learning
        c.execute("""
CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
""")

        # Feedback / learning table - stores specialist corrections so the
        # classifier can learn and auto-apply corrections to similar future
        # tickets
        c.execute("""
CREATE TABLE IF NOT EXISTS feedback(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_text TEXT,
    original_category TEXT,
    corrected_category TEXT,
    original_priority TEXT,
    corrected_priority TEXT,
    corrected_request_type TEXT,
    created_at TEXT
)
""")

        # Ensure the columns exist for databases created with the old schema
        new_columns = {
            "channel": "TEXT",
            "eta": "TEXT",
            "resolved_at": "TEXT",
            "resolution_notes": "TEXT",
            "csat_score": "INTEGER",
            "escalated": "INTEGER DEFAULT 0",
            "escalation_reason": "TEXT",
            "corrected_category": "TEXT",
            "corrected_priority": "TEXT",
            "corrected_request_type": "TEXT",
            "full_result": "TEXT",
        }
        for col, definition in new_columns.items():
            try:
                c.execute(f"ALTER TABLE tickets ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database initialization failed: {e}")


try:
    init_db()
except Exception as e:
    print(f"Failed to initialize database: {e}")

# ==============================
# CSV Seed (Customer Support Dataset)
# ==============================

CSV_PATH = os.path.join(BASE_DIR, "customer_support_tickets.csv")

# Map CSV "Ticket Priority" values to the app's P-level format
CSV_PRIORITY_MAP = {
    "Critical": "P1 - Critical",
    "High": "P2 - High",
    "Medium": "P3 - Medium",
    "Low": "P4 - Low",
}

# Map CSV channels to the app's channel vocabulary
CSV_CHANNEL_MAP = {
    "Email": "email",
    "Chat": "chat",
    "Phone": "phone",
    "Social media": "other",
}

# Map CSV ticket types to the app's category vocabulary
CSV_CATEGORY_MAP = {
    "Technical issue": "Bug / Defect",
    "Billing inquiry": "Billing",
    "Refund request": "Billing",
    "Cancellation request": "Service Request",
    "Product inquiry": "Service Request",
}

# Map CSV ticket status values to the app's status vocabulary
CSV_STATUS_MAP = {
    "Closed": "Resolved",
    "Open": "Open",
    "Pending Customer Response": "Open",
}

# Template placeholders in the CSV that refer to the purchased product.
# The source CSV contains unrendered template tokens (e.g. {product_purchased})
# inside Ticket Description. Substitute them with the real product name so
# tickets read naturally instead of showing literal template syntax.
PRODUCT_PLACEHOLDERS = [
    "{product_purchased}", "{product_name}", "{product}",
    "{product_purchases}", "{products_purchased}", "{products}",
    "{product_purchase}", "{product_purchasing}", "{purchased}",
    "{product_title}", "{product_purchased_name}", "{product_item}",
    "{item}", "{item_name}", "{product_product}",
    "{product_purchased_product}", "{product_product_purchased}",
    "{product_selected}", "{product_for_all}", "{product_item_name}",
    "{product_purchased_device}", "{product_purchased_name}",
]


def _substitute_product_placeholders(ticket_text, product_name):
    """Replace product-name template placeholders with the real product name.

    The source CSV stores literal template tokens like {product_purchased}
    that were never rendered. Use the 'Product Purchased' column value so
    seeded tickets display the actual product name.

    Matching is case-insensitive and also handles angle-bracket variants
    (e.g. <Product_purchased>) and stray duplicate closing braces that appear
    in the raw CSV data.
    """
    if not ticket_text or not product_name:
        return ticket_text
    product = product_name.strip()
    if not product:
        return ticket_text

    cleaned = ticket_text
    for placeholder in PRODUCT_PLACEHOLDERS:
        token = placeholder.strip("{}")
        # Case-insensitive brace variant, including stray duplicate closing
        # braces that appear in the raw CSV (e.g. {Product_Purchased}})
        brace_pattern = re.compile(
            r"\{" + re.escape(token) + r"\}\}?", re.IGNORECASE
        )
        cleaned = brace_pattern.sub(product, cleaned)
        # Case-insensitive angle-bracket variant (<Product_purchased>)
        angle_pattern = re.compile(
            r"<" + re.escape(token) + r">", re.IGNORECASE
        )
        cleaned = angle_pattern.sub(product, cleaned)

    # Catch-all: any remaining product template token (e.g. {product_id},
    # {product_purchased_url}, <product_name>) with no dedicated CSV column
    # gets replaced with the real product name so no raw template syntax
    # shows up in the seeded tickets.
    cleaned = re.sub(
        r"[\{<]\s*product[^}>]*[\}>]", product, cleaned, flags=re.IGNORECASE
    )
    return cleaned


def _csv_to_user_id(customer_email):
    """Use the customer email as the user_id for cross-ticket tracking."""
    return (customer_email or "anonymous").strip().lower()


def _csv_to_timestamp(value):
    """Parse a CSV date/datetime string into ISO format, or return None."""
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            continue
    return None


def _build_seeded_full_result(category, priority, channel, ticket_type, eta):
    """Build a minimal full_result JSON blob matching the app's expected shape."""
    return {
        "categorization": {
            "primary_category": category,
            "request_type": ticket_type or "General",
        },
        "priority": {
            "level": priority,
            "rationale": "Imported from customer_support_tickets.csv",
        },
        "channel": channel,
        "eta": eta,
        "triage": {
            "customer_tier": "Standard",
            "affected_systems": [],
            "reproducibility": "Unknown",
            "sentiment": "Neutral",
        },
        "routing": {
            "suggested_department": "Tier 1",
            "internal_notes": "",
        },
    }


def seed_from_csv():
    """Import customer_support_tickets.csv into the tickets table.

    Idempotent: only runs when the tickets table is empty, so repeated
    startups won't duplicate data. If the CSV is missing, the app simply
    continues with an empty queue.
    """
    if not os.path.exists(CSV_PATH):
        print("No customer_support_tickets.csv found — skipping CSV seed.")
        return

    try:
        conn = sqlite3.connect(DB, timeout=30)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        if count > 0:
            conn.close()
            print(f"Tickets table already has {count} rows — skipping CSV seed.")
            return

        inserted = 0
        skipped = 0
        with open(CSV_PATH, newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                ticket_text = (row.get("Ticket Description") or "").strip()
                if not ticket_text:
                    skipped += 1
                    continue

                # The CSV's Ticket Description contains unrendered template
                # tokens (e.g. {product_purchased}). Substitute them with the
                # real product name from the Product Purchased column so tickets
                # display naturally (e.g. "issue with the GoPro Hero").
                product_name = (row.get("Product Purchased") or "").strip()
                ticket_text = _substitute_product_placeholders(ticket_text, product_name)
                if not ticket_text.strip():
                    skipped += 1
                    continue

                # Preserve the original CSV ticket ID (AUTOINCREMENT is
                # overridden on first insert so rows keep their source ID)
                try:
                    ticket_id = int(row.get("Ticket ID"))
                except (TypeError, ValueError):
                    skipped += 1
                    continue

                user_id = _csv_to_user_id(row.get("Customer Email"))
                channel = CSV_CHANNEL_MAP.get(
                    (row.get("Ticket Channel") or "").strip(), "email"
                )
                priority = CSV_PRIORITY_MAP.get(
                    (row.get("Ticket Priority") or "").strip(), "P4 - Low"
                )
                category = CSV_CATEGORY_MAP.get(
                    (row.get("Ticket Type") or "").strip(), "Service Request"
                )
                status_val = CSV_STATUS_MAP.get(
                    (row.get("Ticket Status") or "").strip(), "Open"
                )
                reason = (row.get("Ticket Subject") or "N/A").strip()
                resolution_notes = (row.get("Resolution") or "").strip()
                csat_raw = (row.get("Customer Satisfaction Rating") or "").strip()
                try:
                    csat_score = int(float(csat_raw)) if csat_raw else None
                except (TypeError, ValueError):
                    csat_score = None

                # Timestamps: prefer First Response Time, fall back to Date of
                # Purchase for created date. Time to Resolution becomes
                # resolved_at for closed tickets.
                timestamp = (
                    _csv_to_timestamp(row.get("First Response Time"))
                    or _csv_to_timestamp(row.get("Date of Purchase"))
                    or datetime.now().isoformat()
                )
                resolved_at = _csv_to_timestamp(row.get("Time to Resolution"))
                if status_val == "Resolved" and not resolved_at:
                    resolved_at = timestamp

                eta = classifier._calculate_eta(priority.split(" ")[0])
                full_result = json.dumps(_build_seeded_full_result(
                    category, priority, channel,
                    (row.get("Ticket Type") or "").strip(), eta,
                ))

                conn.execute(
                    """
                    INSERT OR IGNORE INTO tickets(
                        id, ticket_text, user_id, channel, priority, category,
                        reason, status, timestamp, eta, resolved_at,
                        resolution_notes, csat_score, escalated,
                        escalation_reason, corrected_category,
                        corrected_priority, corrected_request_type, full_result
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ticket_id, ticket_text, user_id, channel, priority,
                        category, reason, status_val, timestamp, eta,
                        resolved_at, resolution_notes, csat_score, 0, None,
                        category, priority, None, full_result,
                    ),
                )
                inserted += 1

        conn.commit()
        conn.close()
        print(f"CSV seed complete: {inserted} tickets inserted, {skipped} skipped.")
    except Exception as e:
        print(f"CSV seed failed: {e}")


# Run the seed after the DB is initialized (no-op if already seeded)
seed_from_csv()


# ==============================
# Learning Helpers
# ==============================

def _tokenize(text):
    """Normalize text into a set of meaningful tokens."""
    tokens = re.findall(r'\b\w+\b', text.lower())
    # Remove very short tokens and common stop words
    stop_words = {'the', 'a', 'an', 'is', 'was', 'to', 'for', 'and', 'i', 'it',
                  'in', 'of', 'on', 'my', 'me', 'can', 'you', 'this', 'that',
                  'with', 'be', 'are', 'or', 'as', 'at', 'by', 'from'}
    return set(t for t in tokens if len(t) > 2 and t not in stop_words)


def _jaccard_similarity(text_a, text_b):
    """Compute Jaccard similarity between two texts based on token sets."""
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a and not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def _find_similar_feedback(feedback_entries, ticket_text):
    """
    Search the feedback table for previously-corrected tickets that are
    semantically similar to the new ticket.

    Returns the best-matching feedback entry if similarity exceeds the
    threshold, otherwise None.
    """
    if not feedback_entries:
        return None

    best_match = None
    best_score = 0.0
    threshold = 0.5  # 50 % token overlap

    for entry in feedback_entries:
        fb_text = entry.get("ticket_text", "")
        if not fb_text:
            continue
        score = _jaccard_similarity(ticket_text, fb_text)
        if score > best_score and score >= threshold:
            best_match = entry
            best_score = score

    return best_match


def _apply_feedback_correction(result, feedback_entry):
    """Override the classification result with a specialist's correction."""
    if feedback_entry.get("corrected_category"):
        result["categorization"]["primary_category"] = feedback_entry["corrected_category"]
    if feedback_entry.get("corrected_priority"):
        result["priority"]["level"] = feedback_entry["corrected_priority"]
        result["priority"]["rationale"] = (
            "Corrected by support specialist (learned from similar ticket)"
        )
    if feedback_entry.get("corrected_request_type"):
        result["categorization"]["request_type"] = feedback_entry["corrected_request_type"]
    return result


# ==============================
# SLA / Status Helpers
# ==============================

def _compute_sla_status(ticket):
    """
    Determine the SLA status of a ticket based on its ETA and resolution state.
      - 'resolved'   : ticket has been resolved
      - 'overdue'    : ETA has passed and ticket is still open
      - 'due_soon'   : ETA is within the warning window (P1/P2 only)
      - 'on_track'   : ETA is in the future, not yet close
    """
    status = (ticket.get("status") or "").lower()
    if status == "resolved":
        return "resolved"

    eta_str = ticket.get("eta")
    if not eta_str:
        return "on_track"

    try:
        eta_dt = datetime.fromisoformat(eta_str)
    except (ValueError, TypeError):
        return "on_track"

    now = datetime.now()
    if now >= eta_dt:
        return "overdue"

    # Warning window: P1 = 5 min, P2 = 30 min, P3 = 1 hr
    priority = (ticket.get("priority") or "").split(" ")[0]
    warning_map = {"P1": 5, "P2": 30, "P3": 60}
    warning_minutes = warning_map.get(priority, 0)
    if warning_minutes:
        warning_delta = timedelta(minutes=warning_minutes)
        if eta_dt - now <= warning_delta:
            return "due_soon"

    return "on_track"


def _row_to_ticket(row):
    """Convert a sqlite Row into a dict and attach computed SLA status."""
    ticket = dict(row)
    ticket["sla_status"] = _compute_sla_status(ticket)
    return ticket


def _store_ticket(ticket_text, user_id, channel, result, reason, status="New",
                  resolution_notes=None, csat_score=None, escalated=0,
                  escalation_reason=None, corrected_category=None,
                  corrected_priority=None, corrected_request_type=None):
    """Persist a single ticket row and return the stored record."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        INSERT INTO tickets(
            ticket_text, user_id, channel, priority, category, reason, status,
            timestamp, eta, resolution_notes, csat_score, escalated,
            escalation_reason, corrected_category, corrected_priority,
            corrected_request_type, full_result
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        ticket_text,
        user_id,
        channel,
        result["priority"]["level"],
        result["categorization"]["primary_category"],
        reason,
        status,
        datetime.now().isoformat(),
        result.get("eta"),
        resolution_notes,
        csat_score,
        escalated,
        escalation_reason,
        corrected_category,
        corrected_priority,
        corrected_request_type,
        json.dumps(result),
    ))
    conn.commit()
    ticket_id = c.lastrowid
    row = conn.execute(
        "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    conn.close()
    return _row_to_ticket(row)


# ==============================
# Flask Routes
# ==============================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/classify", methods=["POST"])
def classify_ticket():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        ticket_text = data.get("text", "")
        user_id = data.get("user_id", "anonymous")
        channel = data.get("channel") or "email"

        if not ticket_text:
            return jsonify({"error": "No ticket text provided"}), 400

        if channel not in CHANNEL_CHOICES:
            channel = "email"

        # 1. Run the base classification
        result = classifier.classify(ticket_text)

        # 2. Learning layer — check if a similar ticket was previously corrected
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        feedback_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC"
            )
        ]
        conn.close()

        matched_feedback = _find_similar_feedback(feedback_rows, ticket_text)
        if matched_feedback:
            result = _apply_feedback_correction(result, matched_feedback)
            result["_learning"] = {
                "applied": True,
                "source": f"Feedback ID #{matched_feedback['id']}",
                "confidence": "high (similarity match)",
            }
        else:
            result["_learning"] = {"applied": False}

        # 3. Calculate ETA based on (possibly corrected) priority
        eta = classifier._calculate_eta(result["priority"]["level"])
        result["eta"] = eta
        result["channel"] = channel

        # 4. Persist the ticket
        ticket = _store_ticket(
            ticket_text, user_id, channel, result,
            result["priority"]["rationale"],
            corrected_category=result.get("categorization", {}).get("primary_category"),
            corrected_priority=result["priority"]["level"],
        )

        return jsonify(ticket)
    except Exception as e:
        print(f"Error in classify route: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tickets/bulk", methods=["POST"])
def classify_bulk():
    """
    Bulk-classify and store multiple tickets at once.
    Expects: { "tickets": [ {"text": "...", "user_id": "...", "channel": "..."}, ... ] }
    Returns: { "results": [ {ticket + classification}, ... ], "summary": {...} }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        items = data.get("tickets", [])
        if not items:
            return jsonify({"error": "No tickets provided"}), 400

        # Fetch feedback once for the learning layer
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        feedback_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC"
            )
        ]
        conn.close()

        saved_tickets = []
        priority_counts = {}

        for item in items:
            if not isinstance(item, dict):
                continue
            ticket_text = (item.get("text") or "").strip()
            if not ticket_text:
                continue
            user_id = item.get("user_id") or "anonymous"
            channel = item.get("channel") or "email"
            if channel not in CHANNEL_CHOICES:
                channel = "email"

            # Base classification
            result = classifier.classify(ticket_text)

            # Learning layer
            matched_feedback = _find_similar_feedback(feedback_rows, ticket_text)
            if matched_feedback:
                result = _apply_feedback_correction(result, matched_feedback)
                result["_learning"] = {
                    "applied": True,
                    "source": f"Feedback ID #{matched_feedback['id']}",
                    "confidence": "high (similarity match)",
                }
            else:
                result["_learning"] = {"applied": False}

            eta = classifier._calculate_eta(result["priority"]["level"])
            result["eta"] = eta
            result["channel"] = channel

            ticket = _store_ticket(
                ticket_text, user_id, channel, result,
                result["priority"]["rationale"],
                corrected_category=result.get("categorization", {}).get("primary_category"),
                corrected_priority=result["priority"]["level"],
            )
            saved_tickets.append(ticket)

            prio = result["priority"]["level"]
            priority_counts[prio] = priority_counts.get(prio, 0) + 1

        return jsonify({
            "count": len(saved_tickets),
            "summary": {
                "total": len(saved_tickets),
                "by_priority": priority_counts,
            },
            "results": saved_tickets,
        })
    except Exception as e:
        print(f"Error in bulk route: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tickets")
def get_tickets():
    """
    Retrieve tickets, optionally filtered.
    Query params:
      - status:   'open' | 'resolved' | 'all'   (default 'all')
      - overdue:  'true'  — only tickets past their ETA
      - priority: 'P1' | 'P2' | ... — filter by P-level
      - category: filter by category name
      - search:   free-text search within ticket_text
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    status_filter = request.args.get("status", "all").lower()
    overdue_only = request.args.get("overdue", "").lower() == "true"
    priority_filter = request.args.get("priority", "").upper()
    category_filter = request.args.get("category", "")
    search = request.args.get("search", "")

    query = "SELECT * FROM tickets"
    conditions = []
    params = []

    if status_filter == "open":
        conditions.append("status != 'Resolved'")
    elif status_filter == "resolved":
        conditions.append("status = 'Resolved'")

    if priority_filter:
        # Match on the P-level prefix
        conditions.append("priority LIKE ? OR priority LIKE ?")
        params.append(f"{priority_filter} %")
        params.append(priority_filter)

    if category_filter:
        conditions.append("category = ?")
        params.append(category_filter)

    if search:
        conditions.append("ticket_text LIKE ?")
        params.append(f"%{search}%")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    rows = conn.execute(query + " ORDER BY id DESC", params).fetchall()
    conn.close()

    tickets = [_row_to_ticket(row) for row in rows]

    # If overdue-only filter, apply it after computing SLA status
    if overdue_only:
        tickets = [t for t in tickets if t["sla_status"] == "overdue"]

    return jsonify(tickets)


@app.route("/api/tickets/<int:ticket_id>", methods=["GET"])
def get_ticket(ticket_id):
    """Retrieve a single ticket by ID."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "Ticket not found"}), 404
    return jsonify(_row_to_ticket(row))


@app.route("/api/tickets/<int:ticket_id>", methods=["PATCH"])
def edit_ticket(ticket_id):
    """
    Allow a support specialist to edit a ticket's classification, status,
    resolution notes, CSAT, and escalation state.
    Any category/priority correction is stored in the feedback table so the
    classifier can learn and apply it to similar future tickets.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row

        # Fetch current ticket
        ticket_row = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if ticket_row is None:
            conn.close()
            return jsonify({"error": "Ticket not found"}), 404

        ticket = dict(ticket_row)
        updates = {}
        corrections = {}  # Track what changed for the feedback table

        # --- Edit category ---
        if "category" in data:
            new_cat = data["category"]
            updates["category"] = new_cat
            updates["corrected_category"] = new_cat
            if ticket.get("category") != new_cat:
                corrections["original_category"] = ticket.get("category")
                corrections["corrected_category"] = new_cat

        # --- Edit priority level ---
        if "priority" in data:
            new_pri = data["priority"]
            updates["priority"] = new_pri
            updates["corrected_priority"] = new_pri
            if ticket.get("priority") != new_pri:
                corrections["original_priority"] = ticket.get("priority")
                corrections["corrected_priority"] = new_pri

        # --- Edit request type ---
        if "request_type" in data:
            updates["corrected_request_type"] = data["request_type"]
            corrections["corrected_request_type"] = data["request_type"]

        # --- Edit status ---
        if "status" in data:
            updates["status"] = data["status"]

        # --- Set / update ETA ---
        if "eta" in data:
            updates["eta"] = data["eta"]

        # --- Resolution notes ---
        if "resolution_notes" in data:
            updates["resolution_notes"] = data["resolution_notes"]

        # --- CSAT score ---
        if "csat_score" in data:
            updates["csat_score"] = data["csat_score"]

        # --- Resolve ticket (single click) ---
        if data.get("resolve"):
            updates["status"] = "Resolved"
            updates["resolved_at"] = datetime.now().isoformat()

        # --- Escalate / de-escalate ---
        if "escalated" in data:
            updates["escalated"] = 1 if data["escalated"] else 0
        if "escalation_reason" in data:
            updates["escalation_reason"] = data["escalation_reason"]

        # --- Update full_result JSON if corrections were made ---
        if corrections:
            try:
                full_result = json.loads(ticket.get("full_result") or "{}")
                if "corrected_category" in corrections:
                    full_result.setdefault("categorization", {})["primary_category"] = corrections["corrected_category"]
                if "corrected_priority" in corrections:
                    full_result.setdefault("priority", {})["level"] = corrections["corrected_priority"]
                    full_result.setdefault("priority", {})["rationale"] = (
                        "Corrected by support specialist"
                    )
                updates["full_result"] = json.dumps(full_result)
            except (json.JSONDecodeError, KeyError):
                pass

        if updates:
            set_clause = ", ".join(f"{col} = ?" for col in updates)
            values = list(updates.values()) + [ticket_id]
            conn.execute(
                f"UPDATE tickets SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()

            # Record the correction in the feedback table for learning
            if corrections:
                conn.execute(
                    """
                    INSERT INTO feedback(
                        ticket_text, original_category, corrected_category,
                        original_priority, corrected_priority,
                        corrected_request_type, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        ticket.get("ticket_text"),
                        corrections.get("original_category"),
                        corrections.get("corrected_category"),
                        corrections.get("original_priority"),
                        corrections.get("corrected_priority"),
                        corrections.get("corrected_request_type"),
                        datetime.now().isoformat(),
                    ),
                )
                conn.commit()

            # Return updated ticket
            updated_row = conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            conn.close()
            return jsonify(_row_to_ticket(updated_row))

        conn.close()
        return jsonify(_row_to_ticket(ticket_row))

    except Exception as e:
        print(f"Error in edit route: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tickets/<int:ticket_id>/escalate", methods=["POST"])
def escalate_ticket(ticket_id):
    """Quickly escalate a critical or complex ticket to senior/engineering."""
    try:
        data = request.get_json() or {}
        reason = data.get("reason", "Escalated to senior team/engineering for deeper investigation.")

        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        ticket_row = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if ticket_row is None:
            conn.close()
            return jsonify({"error": "Ticket not found"}), 404

        conn.execute(
            "UPDATE tickets SET escalated = 1, escalation_reason = ? WHERE id = ?",
            (reason, ticket_id),
        )
        conn.commit()

        # Ensure escalated tickets are also bumped to P1/P2 and routed accordingly
        ticket = dict(ticket_row)
        current_pri = (ticket.get("priority") or "").split(" ")[0]
        if current_pri not in ("P1", "P2"):
            new_pri = "P2 - High" if current_pri == "P3" else "P1 - Critical"
            conn.execute(
                "UPDATE tickets SET priority = ? WHERE id = ?",
                (new_pri, ticket_id),
            )
            conn.commit()

        updated_row = conn.execute(
            "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        conn.close()
        return jsonify(_row_to_ticket(updated_row))

    except Exception as e:
        print(f"Error in escalate route: {e}")
        return jsonify({"error": str(e)}), 500


# ==============================
# Analytics Routes
# ==============================

@app.route("/api/analytics/dashboard", methods=["GET"])
def analytics_dashboard():
    """High-level dashboard summary for the queue overview."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    open_tickets = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status != 'Resolved'"
    ).fetchone()[0]
    resolved = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status = 'Resolved'"
    ).fetchone()[0]

    # Priority breakdown
    priority_rows = conn.execute(
        "SELECT priority, COUNT(*) as cnt FROM tickets GROUP BY priority"
    ).fetchall()
    by_priority = {r["priority"]: r["cnt"] for r in priority_rows}

    # Category breakdown
    category_rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM tickets "
        "WHERE status != 'Resolved' GROUP BY category"
    ).fetchall()
    by_category_open = {r["category"]: r["cnt"] for r in category_rows}

    # Channel breakdown
    channel_rows = conn.execute(
        "SELECT channel, COUNT(*) as cnt FROM tickets "
        "WHERE status != 'Resolved' GROUP BY channel"
    ).fetchall()
    by_channel = {r["channel"]: r["cnt"] for r in channel_rows}

    # CSAT stats
    csat_rows = conn.execute(
        "SELECT csat_score FROM tickets WHERE csat_score IS NOT NULL"
    ).fetchall()
    csat_scores = [r["csat_score"] for r in csat_rows]
    avg_csat = round(sum(csat_scores) / len(csat_scores), 2) if csat_scores else None
    low_csat = sum(1 for s in csat_scores if s <= 2)

    # Overdue count
    rows = conn.execute(
        "SELECT id, priority, status, eta, timestamp, resolved_at FROM tickets ORDER BY id DESC"
    ).fetchall()
    overdue_count = sum(1 for r in rows if _compute_sla_status(dict(r)) == "overdue")
    due_soon_count = sum(1 for r in rows if _compute_sla_status(dict(r)) == "due_soon")

    # Escalated count
    escalated_count = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE escalated = 1 AND status != 'Resolved'"
    ).fetchone()[0]

    # Average resolution time (for resolved tickets)
    resolved_rows = [dict(r) for r in rows if (r["status"] or "").lower() == "resolved"]
    resolution_times = []
    for r in resolved_rows:
        try:
            created = datetime.fromisoformat(r["timestamp"])
            resolved = datetime.fromisoformat(r["resolved_at"])
            delta = (resolved - created).total_seconds() / 3600  # hours
            if delta >= 0:
                resolution_times.append(delta)
        except (ValueError, TypeError):
            pass
    avg_resolution_hours = round(sum(resolution_times) / len(resolution_times), 2) if resolution_times else None

    conn.close()

    return jsonify({
        "total_tickets": total,
        "open_tickets": open_tickets,
        "resolved_total": resolved,
        "overdue_tickets": overdue_count,
        "due_soon_tickets": due_soon_count,
        "escalated_tickets": escalated_count,
        "by_priority": by_priority,
        "by_category_open": by_category_open,
        "by_channel": by_channel,
        "avg_csat": avg_csat,
        "low_csat_count": low_csat,
        "avg_resolution_hours": avg_resolution_hours,
    })


@app.route("/api/analytics/csat", methods=["GET"])
def analytics_csat():
    """CSAT trends and breakdowns for monitoring satisfaction."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # Overall and by category
    rows = conn.execute(
        "SELECT csat_score, category, timestamp FROM tickets "
        "WHERE csat_score IS NOT NULL ORDER BY timestamp DESC"
    ).fetchall()

    scores = [dict(r) for r in rows]
    overall_avg = round(sum(s["csat_score"] for s in scores) / len(scores), 2) if scores else None

    by_category = {}
    for s in scores:
        cat = s.get("category") or "Uncategorized"
        by_category.setdefault(cat, []).append(s["csat_score"])
    by_category_avg = {
        cat: round(sum(vals) / len(vals), 2)
        for cat, vals in by_category.items()
    }

    # Recent scores (last 20)
    recent = [{"score": s["csat_score"], "category": s.get("category"), "timestamp": s.get("timestamp")} for s in scores[:20]]

    conn.close()
    return jsonify({
        "overall_avg": overall_avg,
        "total_rated": len(scores),
        "by_category": by_category_avg,
        "recent": recent,
    })


@app.route("/api/analytics/recurring", methods=["GET"])
def analytics_recurring():
    """
    Flag patterns of recurring complaints.
    Groups tickets by category/request_type and runs keyword frequency
    analysis to surface the most common complaint themes.
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # Group by category and request_type, count open tickets.
    # request_type lives inside the full_result JSON, so we parse it in Python.
    open_rows = conn.execute(
        "SELECT id, category, full_result FROM tickets WHERE status != 'Resolved'"
    ).fetchall()

    type_counts = {}
    for r in open_rows:
        try:
            full = json.loads(r["full_result"] or "{}")
            req_type = full.get("categorization", {}).get("request_type", "Unknown")
        except (json.JSONDecodeError, TypeError):
            req_type = "Unknown"
        key = (r["category"] or "Uncategorized", req_type)
        type_counts[key] = type_counts.get(key, 0) + 1

    recurring_open = [
        {"category": cat, "request_type": rt, "count": cnt}
        for (cat, rt), cnt in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
    ][:10]


    # Keyword frequency analysis across all ticket texts to find common themes
    all_rows = conn.execute(
        "SELECT ticket_text, category FROM tickets"
    ).fetchall()

    keyword_freq = {}
    category_keywords = {}

    important_keywords = [
        "billing", "invoice", "payment", "charge", "refund", "overcharge",
        "login", "password", "access", "mfa", "account",
        "error", "broken", "crash", "slow", "loading", "timeout",
        "server", "downtime", "outage", "api", "database",
        "feature", "request", "suggestion", "feedback",
        "security", "breach", "phishing", "compromised",
        "cancel", "refund", "charged", "double",
    ]

    for r in all_rows:
        text_lower = (r["ticket_text"] or "").lower()
        category = r["category"] or "Uncategorized"
        for kw in important_keywords:
            if kw in text_lower:
                keyword_freq[kw] = keyword_freq.get(kw, 0) + 1
                category_keywords.setdefault(category, {})
                category_keywords[category][kw] = category_keywords[category].get(kw, 0) + 1

    # Top 15 keywords overall
    top_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:15]

    # Categories with most recurring issues (top keywords per category)
    category_patterns = []
    for cat, kw_map in sorted(
        category_keywords.items(),
        key=lambda x: sum(x[1].values()),
        reverse=True,
    )[:5]:
        top_in_cat = sorted(kw_map.items(), key=lambda x: x[1], reverse=True)[:3]
        category_patterns.append({
            "category": cat,
            "top_issues": [{"keyword": k, "count": c} for k, c in top_in_cat],
        })

    # Repeated complainants — user_ids appearing with multiple low CSAT ratings
    csat_rows = conn.execute(
        "SELECT user_id, csat_score FROM tickets "
        "WHERE csat_score IS NOT NULL AND csat_score <= 2 AND user_id != 'anonymous'"
    ).fetchall()
    user_complaints = {}
    for r in csat_rows:
        uid = r["user_id"]
        user_complaints[uid] = user_complaints.get(uid, 0) + 1
    repeat_complainants = [
        {"user_id": uid, "low_csat_count": cnt}
        for uid, cnt in sorted(user_complaints.items(), key=lambda x: x[1], reverse=True)
        if cnt >= 2
    ][:10]

    conn.close()

    return jsonify({
        "recurring_open_by_type": recurring_open,
        "top_complaint_keywords": [{"keyword": k, "count": c} for k, c in top_keywords],
        "category_patterns": category_patterns,
        "repeat_complainants": repeat_complainants,
    })


# ==============================
# Start App
# ==============================

if __name__ == "__main__":
    app.run(debug=True, port=5002)
