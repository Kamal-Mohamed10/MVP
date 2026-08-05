# Support Ticket Triage MVP

An automated ticket triage tool that helps support specialists quickly process,
prioritize, and resolve customer support tickets across email, chat, and phone
channels — replacing the endless morning spreadsheet grind with an intelligent,
SLA-aware queue.

## The Problem

Jordan, a support specialist, was spending **90–120 minutes every morning**
manually reading and sorting 200–300 weekly tickets. Critical issues (like a
**billing problem that sat unresolved for 4 hours**) slipped through because they
looked routine in the subject line. All the data existed — ticket type, channel,
priority, resolution time, CSAT — but there was no way to put it to work.

## What It Does

### 1. Auto-Classification & Priority Assignment
Every ticket is instantly categorized (Security, Billing, Bug, Infrastructure,
Service Request, Feature Request, Incident, General) and assigned a **P1–P5
priority** using an Impact × Urgency matrix with special rules for security
incidents and revenue-impacting billing issues.

### 2. Multi-Channel Support
Tickets carry a **channel** tag (Email, Chat, Phone) so representatives can see
where each request originated and triage accordingly.

### 3. Bulk Processing
Paste dozens of tickets at once (one per line) and they are all classified and
queued in seconds — turning the 1.5-hour morning grind into a 10-second task.

### 4. SLA Tracking & Overdue Alerts
Each priority level has an **ETA** (P1 = 15 min, P2 = 1 hr, P3 = 4 hrs, …).
The dashboard highlights tickets that are **overdue** or **due soon**, so
nothing slips through the cracks again.

### 5. Escalation
Critical or complex tickets can be **escalated** to senior team members or
engineering with one click — automatically bumping the priority and flagging it
for follow-up.

### 6. Customer Satisfaction Monitoring
Log **CSAT scores** (1–5) per ticket. The dashboard shows average CSAT overall
and per category, so you can spot which issue types are leaving customers
frustrated.

### 7. Recurring Complaint Detection
The analytics page surfaces **patterns** — the most common complaint keywords,
which categories have the most open tickets, and which customers repeatedly
report low satisfaction.

### 8. Resolution Notes & Learning
Log **resolution notes** on each ticket so the team can learn from past issues.
When you correct a misclassification, the system **remembers** and automatically
applies the correction to similar future tickets via fuzzy text matching.

## Quick Start

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate      # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py

# 4. Open in your browser
open http://localhost:5001
```

## Postgres migration helper

If you already have a Postgres database and want to migrate existing SQLite data into it, use the helper script:

```bash
DATABASE_URL="postgres://user:password@host:port/dbname" \
  python migrate_sqlite_to_postgres.py --sqlite-path support_tickets.db
```

If you want to clear the destination before migrating, add `--overwrite`:

```bash
DATABASE_URL="postgres://user:password@host:port/dbname" \
  python migrate_sqlite_to_postgres.py --sqlite-path support_tickets.db --overwrite
```

## API Endpoints

| Method | Endpoint                         | Description                              |
|--------|----------------------------------|------------------------------------------|
| POST   | `/api/classify`                  | Classify a single ticket (with channel)  |
| POST   | `/api/tickets/bulk`              | Bulk-classify many tickets at once       |
| GET    | `/api/tickets`                   | List tickets (filters: status, overdue, priority, category, search) |
| GET    | `/api/tickets/<id>`             | Get a single ticket (with SLA status)    |
| PATCH  | `/api/tickets/<id>`              | Edit category/priority/status/ETA/CSAT/notes/escalation |
| GET    | `/api/analytics/dashboard`       | Queue summary, SLA counts, CSAT, resolution time |
| GET    | `/api/analytics/csat`            | CSAT trends and per-category breakdown     |
| GET    | `/api/analytics/recurring`       | Recurring complaint patterns & keywords  |

## How It Works

- **`ticket_classifier.py`** — Rule-based classifier using keyword matching
  for Impact/Urgency scoring, security detection, and routing suggestions.
- **`app.py`** — Flask backend with SQLite persistence, a feedback-learning
  layer (Jaccard similarity on corrected tickets), SLA computation, escalation
  handling, and analytics aggregation.
- **`templates/index.html`** + **`static/`** — Single-page dashboard with live
  queue sorting, dashboard charts, and bulk import.

## Files

```
.
├── app.py                  # Flask backend (routes, DB, analytics, SLA, escalation)
├── ticket_classifier.py    # Rule-based ticket classification engine
├── migrate_sqlite_to_postgres.py  # Helper script to copy SQLite data into Postgres
├── templates/
│   └── index.html          # Dashboard UI
├── static/
│   ├── style.css           # All dashboard/ticket styles
│   └── script.js           # Frontend logic (dashboard, charts, bulk, queue)
├── requirements.txt        # Python dependencies
└── support_tickets.db      # SQLite database (auto-created, gitignored)
```
