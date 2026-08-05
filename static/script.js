/* ================================================================
   Support Specialist Dashboard - Frontend Logic
   ================================================================ */

const PRIORITY_OPTIONS = ["P1 - Critical", "P2 - High", "P3 - Medium", "P4 - Low", "P5 - Low"];
const CATEGORY_OPTIONS = [
    "Security & Compliance",
    "Infrastructure & Hosting",
    "Billing",
    "Bug / Defect",
    "Service Request",
    "Feature Request / Feedback",
    "Incident",
    "General"
];

const CATEGORY_CSS_MAP = {
    "Security & Compliance": "cat-security",
    "Infrastructure & Hosting": "cat-infrastructure",
    "Billing": "cat-billing",
    "Bug / Defect": "cat-bug",
    "Feature Request / Feedback": "cat-feature",
    "Service Request": "cat-service",
    "Incident": "cat-bug",
    "General": ""
};

const CHANNEL_LABELS = {
    "email": "Email",
    "chat": "Chat",
    "phone": "Phone",
    "other": "Other"
};

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function fmtDate(isoStr) {
    if (!isoStr) return "—";
    try {
        return new Date(isoStr).toLocaleString();
    } catch {
        return "—";
    }
}

function priorityClass(level) {
    if (!level) return "priority-p4";
    const m = level.match(/^P(\d)/);
    return m ? "priority-p" + m[1].toLowerCase() : "priority-p4";
}

function priorityLevel(level) {
    if (!level) return "P4";
    const m = level.match(/^P(\d)/);
    return m ? "P" + m[1] : "P4";
}

function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    var map = {
        "&": String.fromCharCode(38, 97, 109, 112, 59),
        "<": String.fromCharCode(38, 108, 116, 59),
        ">": String.fromCharCode(38, 103, 116, 59),
        '"': String.fromCharCode(38, 113, 117, 111, 116, 59),
        "'": String.fromCharCode(38, 35, 51, 57, 59)
    };
    return String(str).replace(/[&<>"']/g, function (ch) { return map[ch]; });
}

function formatClassificationResult(result) {
    if (!result) return "No classification result available.";

    // Parse full_result JSON string if present
    var full = {};
    if (result.full_result) {
        try {
            full = JSON.parse(result.full_result);
        } catch (e) {
            // fall through
        }
    }

    var lines = [];

    // Ticket Summary
    var summary = full.ticket_summary || result.ticket_summary;
    if (summary) {
        lines.push("<strong>Summary:</strong> " + escapeHtml(summary));
    }

    // Categorization
    var cat = full.categorization || result.categorization || {};
    var catParts = [];
    if (cat.primary_category) catParts.push("Category: <strong>" + escapeHtml(cat.primary_category) + "</strong>");
    if (cat.sub_category && cat.sub_category !== cat.primary_category) catParts.push("Sub-category: <strong>" + escapeHtml(cat.sub_category) + "</strong>");
    if (cat.request_type) catParts.push("Request Type: <strong>" + escapeHtml(cat.request_type) + "</strong>");
    if (catParts.length) lines.push(catParts.join("  |  "));

    // Priority
    var pri = full.priority || result.priority || {};
    if (pri.level) {
        var priHtml = "Priority: <strong class='" + priorityClass(pri.level) + "'>" + escapeHtml(pri.level) + "</strong>";
        if (pri.rationale) priHtml += "  <em>(" + escapeHtml(pri.rationale) + ")</em>";
        lines.push(priHtml);
    }

    // Channel
    var channel = full.channel || result.channel;
    if (channel) {
        lines.push("Channel: <strong>" + escapeHtml(CHANNEL_LABELS[channel] || channel) + "</strong>");
    }

    // Triage / Metrics
    var triage = full.triage || result.triage || {};
    var triageParts = [];
    if (triage.customer_tier) triageParts.push("Tier: <strong>" + escapeHtml(triage.customer_tier) + "</strong>");
    if (triage.affected_systems && triage.affected_systems.length) triageParts.push("Systems: <strong>" + escapeHtml(triage.affected_systems.join(", ")) + "</strong>");
    if (triage.reproducibility) triageParts.push("Reproducibility: <strong>" + escapeHtml(triage.reproducibility) + "</strong>");
    if (triage.sentiment) triageParts.push("Sentiment: <strong>" + escapeHtml(triage.sentiment) + "</strong>");
    if (triageParts.length) lines.push(triageParts.join("  |  "));

    // Routing
    var routing = full.routing || result.routing || {};
    if (routing.suggested_department) {
        lines.push("Routing: <strong>" + escapeHtml(routing.suggested_department) + "</strong>");
    }
    if (routing.internal_notes) {
        lines.push("Internal Notes: " + escapeHtml(routing.internal_notes));
    }

    // ETA
    var eta = full.eta || result.eta;
    if (eta) {
        var etaDate = new Date(eta);
        if (!isNaN(etaDate.getTime())) {
            lines.push("Estimated Resolution (ETA): <strong>" + etaDate.toLocaleString() + "</strong>");
        }
    }

    // Learning / Feedback Applied
    var learning = full._learning || result._learning;
    if (learning && learning.applied) {
        lines.push("<span class='learning-badge'>Learned from previous correction: " + escapeHtml(learning.source || "") + "</span>");
    }

    // Customer Response Draft
    var responseDraft = full.customer_response_draft || result.customer_response_draft;
    if (responseDraft) {
        lines.push("<hr/><strong>Suggested Customer Response:</strong><br/>" + escapeHtml(responseDraft).replace(/\n/g, "<br/>"));
    }

    // Reason (top-level field)
    if (result.reason) {
        lines.push("<strong>Reason:</strong> " + escapeHtml(result.reason));
    }

    // Status and ID
    if (result.status) {
        lines.push("Status: <strong>" + escapeHtml(result.status) + "</strong>");
    }
    if (result.id) {
        lines.push("Ticket ID: <strong>" + escapeHtml(result.id) + "</strong>");
    }

    return lines.join("<br/>");
}


function starRating(score) {
    score = Number(score) || 0;

    let html = "";
    for (let i = 1; i <= 5; i++) {
        html += i <= score ? "★" : "☆";
    }
    return '<span class="star-rating" data-score="' + score + '">' + html + "</span>";
}

// ------------------------------------------------------------------
// Dashboard Summary + Charts
// ------------------------------------------------------------------

let _activeFilter = { type: "all" };
let _dashboardRefreshInterval = null;

async function loadDashboard() {
    try {
        const resp = await fetch("/api/analytics/dashboard");
        const data = await resp.json();

        document.getElementById("stat-total").textContent = data.total_tickets || 0;
        document.getElementById("stat-open").textContent = data.open_tickets || 0;
        document.getElementById("stat-overdue").textContent = data.overdue_tickets || 0;
        document.getElementById("stat-due-soon").textContent = data.due_soon_tickets || 0;
        document.getElementById("stat-escalated").textContent = data.escalated_tickets || 0;
        // Fetch resolved count from tickets endpoint to ensure correct integer display
        fetch("/api/tickets?status=resolved")
            .then(r => r.ok ? r.json() : Promise.reject(r))
            .then(resolvedData => {
                const count = Array.isArray(resolvedData) ? resolvedData.length : 0;
                document.getElementById("stat-resolved").textContent = count;
            })
            .catch(() => {
                // Fallback to dashboard data if the fetch fails
                document.getElementById("stat-resolved").textContent = data.resolved_total || 0;
            });

        const csatEl = document.getElementById("stat-csat");
        csatEl.innerHTML = data.avg_csat !== null && data.avg_csat !== undefined
            ? starRating(data.avg_csat) + " " + data.avg_csat.toFixed(1)
            : "—";

        const resEl = document.getElementById("stat-resolution");
        resEl.textContent = (data.avg_resolution_hours !== null && data.avg_resolution_hours !== undefined)
            ? data.avg_resolution_hours.toFixed(1) + "h"
            : "—";

        // Bar charts
        renderBarChart("chart-priority", data.by_priority || {}, "priority");
        renderBarChart("chart-category", data.by_category_open || {}, "category");
        renderBarChart("chart-channel", data.by_channel || {}, "channel");
    } catch (e) {
        console.error("Dashboard load error:", e);
    }
}

function renderBarChart(containerId, data, kind) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const keys = Object.keys(data);
    if (keys.length === 0) {
        el.innerHTML = '<p class="empty-msg">No data</p>';
        return;
    }

    // Sort by count descending
    const entries = keys
        .map(k => ({ key: k, value: data[k] }))
        .sort((a, b) => b.value - a.value);

    const maxVal = Math.max(...entries.map(e => e.value), 1);

    let html = "";
    for (const entry of entries) {
        const pct = Math.round((entry.value / maxVal) * 100);
        let cls = "";
        if (kind === "priority") {
            cls = priorityClass(entry.key);
        } else if (kind === "category") {
            cls = CATEGORY_CSS_MAP[entry.key] || "";
        } else if (kind === "channel") {
            cls = "cat-channel";
        }
        html += '<div class="bar-row">'
            + '<span class="bar-label">' + escapeHtml(entry.key) + '</span>'
            + '<div class="bar-track"><div class="bar-fill ' + cls + '" style="width:' + pct + '%"></div></div>'
            + '<span class="bar-value">' + entry.value + '</span>'
            + '</div>';
    }
    el.innerHTML = html;
}

// ------------------------------------------------------------------
// Recurring Complaints / Insights
// ------------------------------------------------------------------

async function loadRecurring() {
    const el = document.getElementById("recurring-content");
    try {
        const resp = await fetch("/api/analytics/recurring");
        const data = await resp.json();

        let html = "";

        // Top recurring open tickets by type
        if (data.recurring_open_by_type && data.recurring_open_by_type.length) {
            html += '<div class="insight-block">';
            html += '<h4>Most Open Tickets by Type</h4>';
            html += '<ul class="insight-list">';
            for (const item of data.recurring_open_by_type.slice(0, 8)) {
                html += '<li><span class="insight-cat">'
                    + escapeHtml(item.category)
                    + '</span><span class="insight-count">' + item.count + ' open</span></li>';
            }
            html += '</ul></div>';
        }

        // Top complaint keywords
        if (data.top_complaint_keywords && data.top_complaint_keywords.length) {
            html += '<div class="insight-block">';
            html += '<h4>Top Complaint Keywords</h4>';
            const tags = data.top_complaint_keywords
                .map(k => '<span class="keyword-tag">' + escapeHtml(k.keyword)
                    + ' <em>(' + k.count + ')</em></span>')
                .join("");
            html += '<div class="keyword-cloud">' + tags + '</div></div>';
        }

        // Category patterns
        if (data.category_patterns && data.category_patterns.length) {
            html += '<div class="insight-block">';
            html += '<h4>Categories & Common Themes</h4>';
            for (const cp of data.category_patterns.slice(0, 5)) {
                const issues = cp.top_issues
                    .map(ti => escapeHtml(ti.keyword) + " (" + ti.count + ")")
                    .join(", ");
                html += '<div class="pattern-row"><span class="insight-cat">'
                    + escapeHtml(cp.category) + '</span><span class="pattern-issues">'
                    + issues + '</span></div>';
            }
            html += '</div>';
        }

        // Repeat complainants
        if (data.repeat_complainants && data.repeat_complainants.length) {
            html += '<div class="insight-block">';
            html += '<h4>Repeat Complainants (2+ low CSAT)</h4>';
            html += '<ul class="insight-list">';
            for (const rc of data.repeat_complainants) {
                html += '<li><span class="insight-cat">'
                    + escapeHtml(rc.user_id)
                    + '</span><span class="insight-count">'
                    + rc.low_csat_count + ' low ratings</span></li>';
            }
            html += '</ul></div>';
        }

        if (!html) {
            html = '<p class="empty-msg">No data yet. Submit some tickets to see patterns.</p>';
        }
        el.innerHTML = html;
    } catch (e) {
        el.innerHTML = '<p class="empty-msg">Error loading insights.</p>';
        console.error("Recurring load error:", e);
    }
}

// ------------------------------------------------------------------
// Load & render ticket list
// ------------------------------------------------------------------

async function load() {
    let url = "/api/tickets";
    let params = new URLSearchParams();

    if (_activeFilter.type === "priority") {
        params.set("priority", _activeFilter.value);
    } else if (_activeFilter.type === "status") {
        params.set("status", _activeFilter.value);
    } else if (_activeFilter.type === "escalated") {
        // Handled client-side since the API doesn't have a dedicated filter
    } else if (_activeFilter.type === "overdue") {
        params.set("overdue", "true");
    }

    if (params.toString()) {
        url += "?" + params.toString();
    }

    try {
        const response = await fetch(url);
        let tickets = await response.json();

        // Client-side filter for escalated
        if (_activeFilter.type === "escalated") {
            tickets = tickets.filter(t => t.escalated);
        }

        const listElement = document.getElementById("list");

        if (tickets.length === 0) {
            listElement.innerHTML = '<p class="empty-msg">No tickets match the current filter.</p>';
            return;
        }

        // Sort: overdue first, then by priority (P1 top), then newest
        const priorityOrder = {"P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5};
        tickets.sort((a, b) => {
            const sa = a.sla_status === "overdue" ? 0 : 1;
            const sb = b.sla_status === "overdue" ? 0 : 1;
            if (sa !== sb) return sa - sb;
            const pa = priorityOrder[priorityLevel(a.priority)] || 99;
            const pb = priorityOrder[priorityLevel(b.priority)] || 99;
            if (pa !== pb) return pa - pb;
            return 0; // keep server order (newest first)
        });

        listElement.innerHTML = tickets.map(renderTicketCard).join("");
    } catch (e) {
        console.error("Load error:", e);
        document.getElementById("list").innerHTML =
            '<p class="empty-msg">Error loading tickets.</p>';
    }
}

function applyFilter(type) {
    _activeFilter = { type: type };
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    event.target.classList.add("active");
    load();
}

function applyFilterPrio(priority) {
    _activeFilter = { type: "priority", value: priority };
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    // Mark the clicked button
    const btn = event.target;
    btn.classList.add("active");
    load();
}

// ------------------------------------------------------------------
// Render a single ticket card
// ------------------------------------------------------------------

function renderTicketCard(t) {
    const pri = t.priority || "P4 - Low";
    const isResolved = (t.status || "").toLowerCase() === "resolved";
    const created = fmtDate(t.timestamp);
    const eta = fmtDate(t.eta);
    const resolved = fmtDate(t.resolved_at);
    const sla = t.sla_status || "on_track";
    const isOverdue = sla === "overdue";
    const isDueSoon = sla === "due_soon";
    const channel = t.channel || "email";
    const isEscalated = !!t.escalated;

    // Parse full_result for extended info
    var fullResult = {};
    try {
        fullResult = JSON.parse(t.full_result || "{}");
    } catch (e) {
        // fall through
    }

    var triage = fullResult.triage || {};
    var routing = fullResult.routing || {};
    var categorization = fullResult.categorization || {};
    var responseDraft = fullResult.customer_response_draft || "";

    var wasCorrected = t.corrected_category || t.corrected_priority || t.corrected_request_type;
    var learningBadge = "";
    if (fullResult._learning && fullResult._learning.applied) {
        learningBadge = '<span class="learning-badge">Learned from edit</span>';
    }

    var categoryOptions = CATEGORY_OPTIONS.map(function (opt) {
        var selected = opt === (t.category || '') ? 'selected' : '';
        return '<option value="' + opt + '" ' + selected + '>' + opt + '</option>';
    }).join('');

    var priorityOptions = PRIORITY_OPTIONS.map(function (opt) {
        var selected = opt === (t.priority || '') ? 'selected' : '';
        return '<option value="' + opt + '" ' + selected + '>' + opt + '</option>';
    }).join('');

    // CSAT select
    var csat = t.csat_score;
    var csatOptions = '<option value="">CSAT</option>';
    for (var i = 1; i <= 5; i++) {
        csatOptions += '<option value="' + i + '"' + (csat == i ? ' selected' : '') + '>'
            + i + ' - ' + ['Poor', 'Fair', 'OK', 'Good', 'Excellent'][i - 1] + '</option>';
    }

    var resolvedHtml = '';
    if (resolved && resolved !== "—") {
        resolvedHtml = '<div class="date-row"><span class="date-label">Resolved</span><span class="date-value resolved-date">' + resolved + '</span></div>';
    }

    var responseHtml = '';
    if (responseDraft) {
        responseHtml = '<div class="detail-row response-draft"><span class="detail-label">Response:</span><span class="detail-value">' + escapeHtml(responseDraft) + '</span></div>';
    }

    var learningNote = '';
    if (wasCorrected) {
        learningNote = '<div class="learning-note">This correction has been saved and will be applied to similar future tickets.</div>';
    }

    // SLA badge
    var slaBadge = "";
    if (isOverdue) {
        slaBadge = '<span class="sla-badge sla-overdue">OVERDUE</span>';
    } else if (isDueSoon) {
        slaBadge = '<span class="sla-badge sla-due-soon">DUE SOON</span>';
    }

    // Escalation button
    var escalateBtnText = isEscalated ? 'De-escalate' : 'Escalate';
    var escalateBtnClass = isEscalated ? 'btn-unescalate' : 'btn-escalate';

    var resolveBtnText = isResolved ? 'Resolved' : 'Mark Resolved';
    var resolveBtnClass = 'btn-resolve ' + (isResolved ? 'resolved' : '');
    var catClass = CATEGORY_CSS_MAP[t.category || ''] || '';

    // Internal notes
    var internalNotes = routing.internal_notes || "";

    // Resolution notes
    var existingNotes = t.resolution_notes || "";

    var cardClass = 'ticket-card ' + priorityClass(pri) + ' ' + catClass
        + (isResolved ? ' resolved' : '')
        + (isOverdue ? ' sla-overdue-card' : '')
        + (isDueSoon ? ' sla-due-soon-card' : '')
        + (isEscalated ? ' escalated-card' : '');

    return [
        '<div class="' + cardClass + '" id="card-' + t.id + '">',
        '  <div class="ticket-header">',
        '    <div class="ticket-meta-left">',
        '      <span class="priority-badge">' + escapeHtml(pri) + '</span>',
        '      <span class="category-badge">' + escapeHtml(t.category || 'General') + '</span>',
        '      <span class="channel-badge channel-' + channel + '">' + (CHANNEL_LABELS[channel] || channel) + '</span>',
        (slaBadge),
        (learningBadge),
        (wasCorrected ? '      <span class="corrected-badge">Corrected</span>' : ''),
        (isEscalated ? '      <span class="escalated-badge">ESCALATED</span>' : ''),
        '    </div>',
        '    <span class="status-badge status-' + (t.status || 'New').toLowerCase().replace(/\s+/g, '-') + '">',
        '      ' + escapeHtml(t.status || 'New'),
        '    </span>',
        '  </div>',
        '',
        '  <div class="ticket-body">',
        '    <p class="ticket-text">' + escapeHtml(t.ticket_text) + '</p>',
        '    <p class="ticket-user">User: ' + escapeHtml(t.user_id || 'anonymous') + '</p>',
        '    <p class="ticket-reason"><strong>Reason:</strong> ' + escapeHtml(t.reason || 'N/A') + '</p>',
        '  </div>',
        '',
        '  <div class="ticket-dates">',
        '    <div class="date-row"><span class="date-label">Created</span><span class="date-value">' + created + '</span></div>',
        '    <div class="date-row"><span class="date-label">ETA</span><span class="date-value">' + eta + '</span></div>',
        (slaBadge ? '<div class="date-row"><span class="date-label">SLA</span>' + slaBadge + '</div>' : ''),
        resolvedHtml,
        '  </div>',
        '',
        '  <div class="ticket-details">',
        '    <div class="detail-row"><span class="detail-label">Routing:</span><span class="detail-value">' + escapeHtml(routing.suggested_department || 'Tier 1') + '</span></div>',
        '    <div class="detail-row"><span class="detail-label">Type:</span><span class="detail-value">' + escapeHtml(categorization.request_type || 'N/A') + '</span></div>',
        '    <div class="detail-row"><span class="detail-label">Systems:</span><span class="detail-value">' + escapeHtml((triage.affected_systems || []).join(', ')) + '</span></div>',
        (internalNotes ? '<div class="detail-row"><span class="detail-label">Notes:</span><span class="detail-value">' + escapeHtml(internalNotes) + '</span></div>' : ''),
        responseHtml,
        '  </div>',
        '',
        '  <div class="ticket-edit" id="edit-' + t.id + '">',
        '    <div class="edit-row">',
        '      <select class="edit-category" data-id="' + t.id + '">' + categoryOptions + '</select>',
        '      <select class="edit-priority" data-id="' + t.id + '">' + priorityOptions + '</select>',
        '      <select class="edit-csat" data-id="' + t.id + '">' + csatOptions + '</select>',
        '      <button class="btn-save" data-id="' + t.id + '">Save</button>',
        '    </div>',
        '    <div class="edit-row">',
        '      <button class="' + resolveBtnClass + '" data-id="' + t.id + '">' + resolveBtnText + '</button>',
        '      <button class="' + escalateBtnClass + '" data-id="' + t.id + '">' + escalateBtnText + '</button>',
        '    </div>',
        '    <textarea class="edit-notes" data-id="' + t.id + '" placeholder="Resolution notes (helps the team learn)...">' + escapeHtml(existingNotes) + '</textarea>',
        learningNote,
        '  </div>',
        '</div>'
    ].join('');
}

// ------------------------------------------------------------------
// Submit new ticket
// ------------------------------------------------------------------

async function send() {
    var ticketInput = document.getElementById("ticket");
    var userInput = document.getElementById("user");
    var channelSelect = document.getElementById("channel");
    var outputElement = document.getElementById("out");
    var statusMsg = document.getElementById("status-msg");

    if (!ticketInput || !userInput || !outputElement || !statusMsg) {
        console.error("One or more required DOM elements are missing");
        return;
    }

    if (!ticketInput.value.trim()) {
        alert("Please enter a ticket description.");
        return;
    }

    var channel = channelSelect ? channelSelect.value || "email" : "email";

    statusMsg.textContent = "Classifying...";
    statusMsg.className = "status-info";
    outputElement.textContent = "Processing...";

    try {
        var response = await fetch("/api/classify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                text: ticketInput.value,
                user_id: userInput.value || "anonymous",
                channel: channel
            })
        });

        if (!response.ok) {
            var errorData = await response.json();
            throw new Error(errorData.error || ("Server error: " + response.status));
        }

        var result = await response.json();
        outputElement.innerHTML = formatClassificationResult(result);
        statusMsg.textContent = "Classification complete.";
        statusMsg.className = "status-success";

        ticketInput.value = "";
        await Promise.all([load(), loadDashboard(), loadRecurring()]);
    } catch (error) {
        console.error("Error in send():", error);
        statusMsg.textContent = "Error: " + error.message;
        statusMsg.className = "status-error";
        outputElement.textContent = "Failed to classify ticket.";
    }
}

// ------------------------------------------------------------------
// Bulk submit
// ------------------------------------------------------------------

async function sendBulk() {
    var bulkInput = document.getElementById("bulk-input");
    var channelSelect = document.getElementById("bulk-channel");
    var statusMsg = document.getElementById("bulk-status");

    if (!bulkInput || !statusMsg) {
        console.error("Bulk DOM elements missing");
        return;
    }

    var lines = bulkInput.value.split("\n")
        .map(l => l.trim())
        .filter(l => l.length > 0);

    if (lines.length === 0) {
        alert("Please paste at least one ticket.");
        return;
    }

    var channel = channelSelect ? channelSelect.value || "email" : "email";
    var tickets = lines.map(line => ({ text: line, channel: channel }));

    statusMsg.textContent = "Processing " + lines.length + " tickets...";
    statusMsg.className = "status-info";

    try {
        var response = await fetch("/api/tickets/bulk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tickets: tickets })
        });

        if (!response.ok) {
            var errorData = await response.json();
            throw new Error(errorData.error || ("Server error: " + response.status));
        }

        var result = await response.json();
        var summary = result.summary || {};
        var byPrio = summary.by_priority || {};

        var priText = Object.keys(byPrio).map(function (k) {
            return k + ": " + byPrio[k];
        }).join(" | ");

        statusMsg.textContent = "Processed " + result.count + " tickets. " + priText;
        statusMsg.className = "status-success";

        bulkInput.value = "";
        await Promise.all([load(), loadDashboard(), loadRecurring()]);
    } catch (error) {
        console.error("Error in sendBulk():", error);
        statusMsg.textContent = "Error: " + error.message;
        statusMsg.className = "status-error";
    }
}

// ------------------------------------------------------------------
// Save edit (category / priority / CSAT / notes correction)
// ------------------------------------------------------------------

async function saveEdit(ticketId, category, priority, csat, notes) {
    try {
        var body = {};
        if (category) body.category = category;
        if (priority) body.priority = priority;
        if (csat !== null && csat !== undefined && csat !== "") body.csat_score = parseInt(csat, 10);
        if (notes !== null && notes !== undefined) body.resolution_notes = notes;

        var response = await fetch("/api/tickets/" + ticketId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });

        if (!response.ok) {
            var errorData = await response.json();
            throw new Error(errorData.error || ("Server error: " + response.status));
        }

        await Promise.all([load(), loadDashboard(), loadRecurring()]);
    } catch (error) {
        console.error("Error saving edit:", error);
        alert("Failed to save edit: " + error.message);
    }
}

// ------------------------------------------------------------------
// Resolve ticket (with optional CSAT + resolution notes)
// ------------------------------------------------------------------

async function resolveTicket(ticketId) {
    try {
        // Gather CSAT and notes from the card
        var csatSelect = document.querySelector(".edit-csat[data-id='" + ticketId + "']");
        var notesTextarea = document.querySelector(".edit-notes[data-id='" + ticketId + "']");
        var csat = csatSelect ? csatSelect.value : null;
        var notes = notesTextarea ? notesTextarea.value : null;

        var body = { resolve: true };
        if (csat) body.csat_score = parseInt(csat, 10);
        if (notes) body.resolution_notes = notes;

        var response = await fetch("/api/tickets/" + ticketId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });

        if (!response.ok) {
            var errorData = await response.json();
            throw new Error(errorData.error || ("Server error: " + response.status));
        }

        await Promise.all([load(), loadDashboard(), loadRecurring()]);
    } catch (error) {
        console.error("Error resolving ticket:", error);
        alert("Failed to resolve ticket: " + error.message);
    }
}

// ------------------------------------------------------------------
// Escalate / de-escalate ticket
// ------------------------------------------------------------------

async function toggleEscalate(ticketId) {
    try {
        var card = document.getElementById("card-" + ticketId);
        var currentlyEscalated = card && card.classList.contains("escalated-card");
        var reason = currentlyEscalated
            ? ""
            : "Escalated to senior team/engineering for deeper investigation.";

        var response = await fetch("/api/tickets/" + ticketId, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                escalated: currentlyEscalated ? 0 : 1,
                escalation_reason: reason
            })
        });

        if (!response.ok) {
            var errorData = await response.json();
            throw new Error(errorData.error || ("Server error: " + response.status));
        }

        await Promise.all([load(), loadDashboard(), loadRecurring()]);
    } catch (error) {
        console.error("Error toggling escalation:", error);
        alert("Failed to toggle escalation: " + error.message);
    }
}

// ------------------------------------------------------------------
// Event delegation for dynamically rendered buttons
// ------------------------------------------------------------------

document.addEventListener("click", function (e) {
    var saveBtn = e.target.closest(".btn-save");
    if (saveBtn) {
        var ticketId = saveBtn.dataset.id;
        var categorySelect = document.querySelector(".edit-category[data-id='" + ticketId + "']");
        var prioritySelect = document.querySelector(".edit-priority[data-id='" + ticketId + "']");
        var csatSelect = document.querySelector(".edit-csat[data-id='" + ticketId + "']");
        var notesTextarea = document.querySelector(".edit-notes[data-id='" + ticketId + "']");
        var category = categorySelect ? categorySelect.value : null;
        var priority = prioritySelect ? prioritySelect.value : null;
        var csat = csatSelect ? csatSelect.value : null;
        var notes = notesTextarea ? notesTextarea.value : null;
        saveEdit(ticketId, category, priority, csat, notes);
        return;
    }

    var resolveBtn = e.target.closest(".btn-resolve");
    if (resolveBtn) {
        var resolveId = resolveBtn.dataset.id;
        var card = document.getElementById("card-" + resolveId);
        if (card && card.classList.contains("resolved")) {
            return; // already resolved, ignore
        }
        resolveTicket(resolveId);
        return;
    }

    var escalateBtn = e.target.closest(".btn-escalate");
    if (escalateBtn) {
        var escId = escalateBtn.dataset.id;
        toggleEscalate(escId);
        return;
    }

    var unescalateBtn = e.target.closest(".btn-unescalate");
    if (unescalateBtn) {
        var unescId = unescalateBtn.dataset.id;
        toggleEscalate(unescId);
        return;
    }
});

// ------------------------------------------------------------------
// Initialize
// ------------------------------------------------------------------

load();
loadDashboard();
loadRecurring();

// Auto-refresh (tickets, dashboard, recurring) every 5 seconds for near-real-time updates.
// Skip refresh while user is actively editing a ticket to avoid losing typed changes.
function _isUserEditing() {
    const editableSelectors = [
        '.edit-notes', '.edit-category', '.edit-priority', '.edit-csat'
    ];
    for (const sel of editableSelectors) {
        const els = document.querySelectorAll(sel);
        for (const el of els) {
            if (el === document.activeElement || el.contains(document.activeElement)) {
                return true;
            }
        }
    }
    return false;
}

setInterval(function() {
    // Only refresh when page is visible and user is not in the middle of an edit
    if (document.hidden) return;
    if (_isUserEditing()) return;
    Promise.all([load(), loadDashboard(), loadRecurring()]).catch(e => console.error('Auto-refresh error:', e));
}, 5000);
