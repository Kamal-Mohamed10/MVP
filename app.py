import os
# Defensive: remove Gemini API key from the environment if present to avoid
# serverless import-time crashes on Vercel where a stray GEMINI_API_KEY
# might cause a third-party client to fail during function initialization.
# If you need Gemini in production, comment out this block and ensure the
# client is initialized with proper error handling and environment checks.
if "GEMINI_API_KEY" in os.environ:
    print("Warning: GEMINI_API_KEY detected in environment — removing to prevent serverless function crashes.")
    os.environ.pop("GEMINI_API_KEY", None)

import re
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify

# Try to import TicketClassifier from local module; fallback to a simple stub
try:
    from ticket_classifier import TicketClassifier  # type: ignore
except ModuleNotFoundError:
    print("Warning: ticket_classifier module not found — using fallback stub TicketClassifier.")
    class TicketClassifier:
        def __init__(self):
            pass

        def classify(self, text):
            """Return a minimal classification result so the app can operate.

            This fallback mirrors the shape expected by app.py but with safe,
            conservative defaults. The production classifier should provide
            richer fields when available.
            """
            priority_level = "P4 - Low"
            eta = self._calculate_eta(priority_level)
            return {
                "ticket_summary": text[:100] + ("..." if len(text) > 100 else ""),
                "categorization": {"primary_category": "General", "sub_category": "General", "request_type": "Service Request"},
                "priority": {"level": priority_level, "rationale": "Fallback classifier (module missing)"},
                "triage": {"customer_tier": "Free / Starter", "affected_systems": ["General System"], "reproducibility": "Vague report", "sentiment": "Neutral"},
                "routing": {"suggested_department": "Tier 1 Support", "internal_notes": "Fallback classifier in use."},
                "eta": eta,
                "customer_response_draft": "Thank you for your message. We will review your request and respond within 24 hours."
            }

        def _calculate_eta(self, priority_level: str) -> str:
            """Calculate ETA isoformat string based on P-level like the production classifier.

            Accepts a priority string such as 'P1 - Critical' and returns an ISO datetime.
            """
            level = priority_level.split(" ")[0] if priority_level else "P4"
            eta_map = {
                "P1": timedelta(minutes=15),
                "P2": timedelta(hours=1),
                "P3": timedelta(hours=4),
                "P4": timedelta(hours=24),
                "P5": timedelta(hours=48),
            }
            eta_delta = eta_map.get(level, timedelta(hours=24))
            eta = datetime.now() + eta_delta
            return eta.isoformat()

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# Initialize the local classifier
classifier = TicketClassifier()

# ==============================
# Database Setup
# ==============================

# Database configuration: use DATABASE_URL (Postgres) if provided, otherwise fall back to local SQLite file path (SUPPORT_DB_PATH)
from sqlalchemy import create_engine, text

SUPPORT_DB_FILE = os.environ.get("SUPPORT_DB_PATH", "support_tickets.db")
DATABASE_URL = os.environ.get("DATABASE_URL")

# If DATABASE_URL provided, use it (Postgres). Otherwise use sqlite file (which may be /tmp/support_tickets.db)
if DATABASE_URL:
    db_url = DATABASE_URL
else:
    # Resolve SUPPORT_DB_FILE similar to before
    if os.path.isabs(SUPPORT_DB_FILE):
        sqlite_path = SUPPORT_DB_FILE
    else:
        # prefer /tmp when available
        sqlite_path = os.path.join("/tmp", os.path.basename(SUPPORT_DB_FILE)) if os.path.exists("/tmp") else SUPPORT_DB_FILE
    db_url = f"sqlite:///{sqlite_path}"

# Create SQLAlchemy engine
engine_kwargs = {}
if db_url.startswith("sqlite:///"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(db_url, **engine_kwargs)

# Helper flags
USING_POSTGRES = DATABASE_URL is not None and (DATABASE_URL.startswith("postgres") or DATABASE_URL.startswith("postgresql"))

CHANNEL_CHOICES = ["email", "chat", "phone", "other"]


def init_db():
    """Initialize DB schema using SQLAlchemy. Safe to call on import."""
    # Create tables if they do not exist
    tickets_ddl = """
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
    """

    feedback_ddl = """
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
    """

    with engine.begin() as conn:
        conn.execute(text(tickets_ddl))
        conn.execute(text(feedback_ddl))


def execute_fetchall(sql, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(r) for r in result.mappings().all()]


def execute_fetchone(sql, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        row = result.mappings().first()
        return dict(row) if row else None


def execute_commit(sql, params=None, returning=False):
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        # If caller requested a returning value (Postgres RETURNING id), return it
        if returning:
            try:
                row = result.fetchone()
                return row[0] if row else None
            except Exception:
                return None
        # Fallback: try to return lastrowid for sqlite
        try:
            return result.lastrowid
        except Exception:
            return None


init_db()


# ==============================
# Health & Debug Routes
# ==============================

@app.route("/api/health", methods=["GET"])
def health():
    """Health check and debug info — shows which DB is being used."""
    return jsonify({
        "status": "ok",
        "db_url": db_url,
        "using_postgres": USING_POSTGRES,
        "note": "If using_postgres is false and db_url points to a tmp or memory SQLite file, data may be ephemeral on serverless platforms."
    })


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
    params = {
        "ticket_text": ticket_text,
        "user_id": user_id,
        "channel": channel,
        "priority": result["priority"]["level"],
        "category": result["categorization"]["primary_category"],
        "reason": reason,
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "eta": result.get("eta"),
        "resolution_notes": resolution_notes,
        "csat_score": csat_score,
        "escalated": escalated,
        "escalation_reason": escalation_reason,
        "corrected_category": corrected_category,
        "corrected_priority": corrected_priority,
        "corrected_request_type": corrected_request_type,
        "full_result": json.dumps(result),
    }

    if USING_POSTGRES:
        sql = """
        INSERT INTO tickets(
            ticket_text, user_id, channel, priority, category, reason, status,
            timestamp, eta, resolution_notes, csat_score, escalated,
            escalation_reason, corrected_category, corrected_priority,
            corrected_request_type, full_result
        ) VALUES (
            :ticket_text, :user_id, :channel, :priority, :category, :reason, :status,
            :timestamp, :eta, :resolution_notes, :csat_score, :escalated,
            :escalation_reason, :corrected_category, :corrected_priority,
            :corrected_request_type, :full_result
        ) RETURNING id
        """
        ticket_id = execute_commit(sql, params=params, returning=True)
    else:
        sql = """
        INSERT INTO tickets(
            ticket_text, user_id, channel, priority, category, reason, status,
            timestamp, eta, resolution_notes, csat_score, escalated,
            escalation_reason, corrected_category, corrected_priority,
            corrected_request_type, full_result
        ) VALUES (
            :ticket_text, :user_id, :channel, :priority, :category, :reason, :status,
            :timestamp, :eta, :resolution_notes, :csat_score, :escalated,
            :escalation_reason, :corrected_category, :corrected_priority,
            :corrected_request_type, :full_result
        )
        """
        ticket_id = execute_commit(sql, params=params)

    row = execute_fetchone("SELECT * FROM tickets WHERE id = :id", {"id": ticket_id})
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
        feedback_rows = execute_fetchall("SELECT * FROM feedback ORDER BY created_at DESC")
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
        feedback_rows = execute_fetchall("SELECT * FROM feedback ORDER BY created_at DESC")

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

    status_filter = request.args.get("status", "all").lower()
    overdue_only = request.args.get("overdue", "").lower() == "true"
    priority_filter = request.args.get("priority", "").upper()
    category_filter = request.args.get("category", "")
    search = request.args.get("search", "")

    query = "SELECT * FROM tickets"
    conditions = []
    params = {}

    if status_filter == "open":
        conditions.append("status != 'Resolved'")
    elif status_filter == "resolved":
        conditions.append("status = 'Resolved'")

    if priority_filter:
        # Match on the P-level prefix
        conditions.append("(priority LIKE :prio_prefix OR priority = :prio_exact)")
        params["prio_prefix"] = f"{priority_filter} %"
        params["prio_exact"] = priority_filter

    if category_filter:
        conditions.append("category = :category")
        params["category"] = category_filter

    if search:
        conditions.append("ticket_text LIKE :search")
        params["search"] = f"%{search}%"

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    rows = execute_fetchall(query + " ORDER BY id DESC", params)

    tickets = [_row_to_ticket(row) for row in rows]

    # If overdue-only filter, apply it after computing SLA status
    if overdue_only:
        tickets = [t for t in tickets if t["sla_status"] == "overdue"]

    return jsonify(tickets)


@app.route("/api/tickets/<int:ticket_id>", methods=["GET"])
def get_ticket(ticket_id):
    """Retrieve a single ticket by ID."""
    row = execute_fetchone("SELECT * FROM tickets WHERE id = :id", {"id": ticket_id})
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

        # Fetch current ticket
        ticket_row = execute_fetchone("SELECT * FROM tickets WHERE id = :id", {"id": ticket_id})
        if ticket_row is None:
            # Diagnostic: include a brief sample of existing ticket ids to help debug
            try:
                sample = execute_fetchall("SELECT id, status FROM tickets ORDER BY id DESC LIMIT 5")
            except Exception:
                sample = []
            print(f"edit_ticket: ticket_id={ticket_id} not found. sample_ids={[r.get('id') for r in sample]}")
            return jsonify({"error": "Ticket not found", "attempted_id": ticket_id, "sample_tickets": sample}), 404

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
            set_clause = ", ".join(f"{col} = :{col}" for col in updates.keys())
            params = dict(updates)
            params["id"] = ticket_id
            execute_commit(f"UPDATE tickets SET {set_clause} WHERE id = :id", params=params)

            # Record the correction in the feedback table for learning
            if corrections:
                fb_params = {
                    "ticket_text": ticket.get("ticket_text"),
                    "original_category": corrections.get("original_category"),
                    "corrected_category": corrections.get("corrected_category"),
                    "original_priority": corrections.get("original_priority"),
                    "corrected_priority": corrections.get("corrected_priority"),
                    "corrected_request_type": corrections.get("corrected_request_type"),
                    "created_at": datetime.now().isoformat(),
                }
                execute_commit(
                    """
                    INSERT INTO feedback(
                        ticket_text, original_category, corrected_category,
                        original_priority, corrected_priority,
                        corrected_request_type, created_at
                    ) VALUES (
                        :ticket_text, :original_category, :corrected_category,
                        :original_priority, :corrected_priority,
                        :corrected_request_type, :created_at
                    )
                    """,
                    params=fb_params,
                )

            # Return updated ticket
            updated_row = execute_fetchone("SELECT * FROM tickets WHERE id = :id", {"id": ticket_id})
            return jsonify(_row_to_ticket(updated_row))

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

        ticket_row = execute_fetchone("SELECT * FROM tickets WHERE id = :id", {"id": ticket_id})
        if ticket_row is None:
            try:
                sample = execute_fetchall("SELECT id, status FROM tickets ORDER BY id DESC LIMIT 5")
            except Exception:
                sample = []
            print(f"escalate_ticket: ticket_id={ticket_id} not found. sample_ids={[r.get('id') for r in sample]}")
            return jsonify({"error": "Ticket not found", "attempted_id": ticket_id, "sample_tickets": sample}), 404

        execute_commit(
            "UPDATE tickets SET escalated = 1, escalation_reason = :reason WHERE id = :id",
            params={"reason": reason, "id": ticket_id},
        )

        # Ensure escalated tickets are also bumped to P1/P2 and routed accordingly
        ticket = dict(ticket_row)
        current_pri = (ticket.get("priority") or "").split(" ")[0]
        if current_pri not in ("P1", "P2"):
            new_pri = "P2 - High" if current_pri == "P3" else "P1 - Critical"
            execute_commit(
                "UPDATE tickets SET priority = :priority WHERE id = :id",
                params={"priority": new_pri, "id": ticket_id},
            )

        updated_row = execute_fetchone("SELECT * FROM tickets WHERE id = :id", {"id": ticket_id})
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
    total_row = execute_fetchone("SELECT COUNT(*) AS cnt FROM tickets") or {"cnt": 0}
    total = total_row.get("cnt", 0)
    open_row = execute_fetchone("SELECT COUNT(*) AS cnt FROM tickets WHERE status != 'Resolved'") or {"cnt": 0}
    open_tickets = open_row.get("cnt", 0)
    resolved_row = execute_fetchone("SELECT COUNT(*) AS cnt FROM tickets WHERE status = 'Resolved'") or {"cnt": 0}
    resolved = resolved_row.get("cnt", 0)

    # Priority breakdown
    priority_rows = execute_fetchall("SELECT priority, COUNT(*) as cnt FROM tickets GROUP BY priority")
    by_priority = {r.get("priority"): r.get("cnt") for r in priority_rows}

    # Category breakdown
    category_rows = execute_fetchall("SELECT category, COUNT(*) as cnt FROM tickets WHERE status != 'Resolved' GROUP BY category")
    by_category_open = {r.get("category"): r.get("cnt") for r in category_rows}

    # Channel breakdown
    channel_rows = execute_fetchall("SELECT channel, COUNT(*) as cnt FROM tickets WHERE status != 'Resolved' GROUP BY channel")
    by_channel = {r.get("channel"): r.get("cnt") for r in channel_rows}

    # CSAT stats
    csat_rows = execute_fetchall("SELECT csat_score FROM tickets WHERE csat_score IS NOT NULL")
    csat_scores = [r.get("csat_score") for r in csat_rows if r.get("csat_score") is not None]
    avg_csat = round(sum(csat_scores) / len(csat_scores), 2) if csat_scores else None
    low_csat = sum(1 for s in csat_scores if s <= 2)

    # Overdue & due soon
    rows = execute_fetchall("SELECT id, priority, status, eta, timestamp, resolved_at FROM tickets ORDER BY id DESC")
    overdue_count = sum(1 for r in rows if _compute_sla_status(r) == "overdue")
    due_soon_count = sum(1 for r in rows if _compute_sla_status(r) == "due_soon")

    # Escalated count
    escalated_row = execute_fetchone("SELECT COUNT(*) AS cnt FROM tickets WHERE escalated = 1 AND status != 'Resolved'") or {"cnt": 0}
    escalated_count = escalated_row.get("cnt", 0)

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

    # Overall and by category
    rows = execute_fetchall(
        "SELECT csat_score, category, timestamp FROM tickets "
        "WHERE csat_score IS NOT NULL ORDER BY timestamp DESC"
    )

    scores = [r for r in rows]
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

    # Group by category and request_type, count open tickets.
    # request_type lives inside the full_result JSON, so we parse it in Python.
    open_rows = execute_fetchall("SELECT id, category, full_result FROM tickets WHERE status != 'Resolved'")

    type_counts = {}
    for r in open_rows:
        try:
            full = json.loads(r.get("full_result") or "{}")
            req_type = full.get("categorization", {}).get("request_type", "Unknown")
        except (json.JSONDecodeError, TypeError):
            req_type = "Unknown"
        key = (r.get("category") or "Uncategorized", req_type)
        type_counts[key] = type_counts.get(key, 0) + 1

    recurring_open = [
        {"category": cat, "request_type": rt, "count": cnt}
        for (cat, rt), cnt in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
    ][:10]


    # Keyword frequency analysis across all ticket texts to find common themes
    all_rows = execute_fetchall("SELECT ticket_text, category FROM tickets")

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
        text_lower = (r.get("ticket_text") or "").lower()
        category = r.get("category") or "Uncategorized"
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
    csat_rows = execute_fetchall(
        "SELECT user_id, csat_score FROM tickets "
        "WHERE csat_score IS NOT NULL AND csat_score <= 2 AND user_id != 'anonymous'"
    )
    user_complaints = {}
    for r in csat_rows:
        uid = r.get("user_id")
        user_complaints[uid] = user_complaints.get(uid, 0) + 1
    repeat_complainants = [
        {"user_id": uid, "low_csat_count": cnt}
        for uid, cnt in sorted(user_complaints.items(), key=lambda x: x[1], reverse=True)
        if cnt >= 2
    ][:10]

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
    app.run(debug=True, port=5001)
