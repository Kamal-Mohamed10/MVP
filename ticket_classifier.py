import re
import json
from datetime import datetime, timedelta


class TicketClassifier:
    def __init__(self):
        # Mapping keywords to Impact (1-3) and Urgency (1-3)
        # Impact: 3=High, 2=Medium, 1=Low
        # Urgency: 3=High, 2=Medium, 1=Low
        self.impact_keywords = {
            # High Impact (3)
            "everyone": 3, "all users": 3, "system-wide": 3, "company": 3, "vip": 3, "executive": 3,
            "domain administrator": 3, "domain admin": 3, "c-level": 3, "financial system": 3,
            "payroll": 3, "wire transfer": 3, "outage": 3, "all customers": 3, "entire org": 3,
            # Medium Impact (2)
            "department": 2, "team": 2, "core feature": 2, "financial": 2, "billing": 2,
            "database": 2, "multiple users": 2,
            # Low Impact (1)
            "single user": 1, "me": 1, "my account": 1, "cosmetic": 1, "minor": 1, "individual": 1
        }

        self.urgency_keywords = {
            # High Urgency (3)
            "immediate": 3, "asap": 3, "urgent": 3, "emergency": 3, "critical": 3, "now": 3,
            "immediately": 3, "blocks": 3, "blocked": 3, "cannot": 3, "unable": 3, "can't": 3,
            "cant": 3, "down": 3, "crashed": 3, "crashing": 3,
            # Medium Urgency (2)
            "quickly": 2, "soon": 2, "degraded": 2, "inconvenient": 2, "workaround": 2,
            # Low Urgency (1)
            "whenever": 1, "eventually": 1, "routine": 1, "general inquiry": 1, "scheduled": 1
        }

        # Security Keywords for strict compliance rules
        # P1: Critical Security Outage — definitive, confirmed security incidents
        self.p1_security_keywords = [
            "ransomware detected", "ransomware on", "ransomware hitting",
            "hacked", "production hacked", "system hacked",
            "data breach", "data leak", "data exfiltration",
            "unauthorized access to production", "database exposed",
            "soc2 violation", "soc 2 violation",
            "active exploit", "currently being exploited",
            "malware infection", "malware on",
            "admin account compromised", "administrator account compromised",
            "domain admin compromised", "domain administrator compromised",
            "production database breached", "full database dump",
            "customer data exposed", "credit card data leaked"
        ]

        # P2: Suspected Security Incident — possible but not confirmed
        self.p2_security_keywords = [
            "phishing email", "phishing attempt", "phishing link", "phishing message",
            "suspicious login attempt", "unrecognized login", "unauthorised login",
            "api key exposed", "api key leaked",
            "lost company laptop", "lost my company", "lost company device",
            "lost my device", "stolen laptop", "stolen device",
            "unauthorized download", "weird email link clicked",
            "suspected breach", "possible breach", "potential breach",
            "credentials leaked", "password dumped"
        ]

        # Security Ambiguity — vague claims that need verification
        self.security_ambiguity_keywords = [
            "i think my account was hacked", "i think my account is hacked",
            "my account might be hacked", "my account may have been hacked",
            "someone accessed my files", "someone might have accessed",
            "suspicious behavior", "suspicious activity",
            "strange activity on my account", "weird activity",
            "possible account compromise", "concerned about account compromise",
            "i think someone accessed",
            "security concern", "i'm worried about security",
            "might have been breached", "could be a breach",
            "possibly compromised", "think it was compromised"
        ]

        # Security Routine — non-urgent security/access requests
        self.security_routine_keywords = [
            "reset mfa", "mfa reset", "reset my mfa",
            "password reset", "reset my password",
            "requesting security logs", "security log request",
            "general compliance inquiry", "soc2 report request",
            "soc 2 report request", "compliance report request",
            "enable mfa", "setup mfa", "configure mfa"
        ]

        self.sentiment_keywords = {
            "angry": "Distressed", "frustrated": "Distressed", "broken": "Distressed",
            "fail": "Distressed", "failing": "Distressed",
            "error": "Neutral", "issue": "Neutral", "problem": "Neutral",
            "cannot": "Neutral", "unable": "Neutral",
            "stop": "Distressed", "crash": "Distressed", "crashing": "Distressed",
            "down": "Distressed", "outage": "Distressed",
            "demanding": "Demanding", "immediately": "Demanding", "asap": "Demanding"
        }

        self.category_map = {
            "Security / Compliance": ["hack", "breach", "password", "virus", "security",
                                       "stolen", "unauthorized", "compromised", "access",
                                       "login", "fraud", "phishing", "malware"],
            "Bug / Defect": ["error", "broken", "crash", "failed", "bug", "not working",
                              "issue", "down", "outage", "slow", "loading", "api",
                              "server", "database", "connection", "timeout"],
            "Service Request": ["access", "hardware", "account", "update",
                                "configuration", "request", "add", "new"],
            "Feature Request / Feedback": ["suggestion", "feedback", "improve",
                                           "would be great", "can you", "request"],
            "Incident": ["interruption", "reduction", "broken", "down", "outage", "crash"],
            "Billing": ["billing", "invoice", "payment", "charge", "receipt",
                        "billing cycle", "refund", "overcharge", "subscription"]
        }

    def _get_score(self, text: str, keyword_map: dict) -> int:
        text = text.lower()
        max_score = 1  # Default minimum
        for keyword, score in keyword_map.items():
            if keyword in text:
                max_score = max(max_score, score)
        return max_score

    def _determine_priority(self, impact: int, urgency: int) -> tuple:
        """Returns (priority_level, rationale) based on Impact and Urgency scores."""
        if impact == 3 and urgency == 3:
            return "P1 - Critical", "High Impact + High Urgency"
        if (impact == 3 and urgency == 2) or (impact == 2 and urgency == 3):
            return "P2 - High", "High Impact + Med Urgency OR Med Impact + High Urgency"
        if (impact == 2 and urgency == 2) or (impact == 1 and urgency == 3):
            return "P3 - Medium", "Med Impact + Med Urgency OR Low Impact + High Urgency"
        return "P4 - Low", "Low Impact + Low Urgency"

    def _determine_priority_from_level(self, level: str) -> str:
        """Extract the P-level from a priority string like 'P1 - Critical'."""
        return level.split(" ")[0] if level else "P4"

    def _extract_metrics(self, text: str):
        text = text.lower()
        tier = "Free / Starter"
        if any(k in text for k in ["enterprise", "vip", "executive", "c-level",
                                     "domain administrator", "domain admin"]):
            tier = "Enterprise / VIP"
        elif "mid-market" in text or "business" in text:
            tier = "Mid-Market"

        systems = []
        system_keywords = {
            "Billing API": ["billing", "invoice", "payment", "charge", "receipt"],
            "User Authentication": ["login", "password", "access", "account",
                                   "sign in", "sign up", "mfa", "mfa", "credentials"],
            "UI/Dashboard": ["button", "layout", "design", "interface", "font",
                             "display", "screen", "look", "feel", "sandbox"],
            "Infrastructure": ["server", "database", "api", "connection", "network",
                               "sso", "hosting", "uptime", "performance"],
            "Core Service": ["app", "service", "system", "platform", "portal"]
        }
        for system, keywords in system_keywords.items():
            if any(k in text for k in keywords):
                systems.append(system)
        if not systems:
            systems.append("General System")

        reproducibility = "Vague report"
        if any(k in text for k in ["steps", "reproduce", "documented", "how to", "repro",
                                    "reproducible", "consistently", "every time",
                                    "always", "happens every"]):
            reproducibility = "Fully documented steps"
        elif any(k in text for k in ["maybe", "sometimes", "not sure", "i think", "possibly",
                                      "occasionally", "intermittently"]):
            reproducibility = "Missing details"

        sentiment = "Neutral"
        for kw, val in self.sentiment_keywords.items():
            if kw in text:
                sentiment = val
                break

        return {
            "tier": tier,
            "systems": systems,
            "reproducibility": reproducibility,
            "sentiment": sentiment
        }

    def _calculate_eta(self, priority_level: str) -> str:
        """Calculate an estimated resolution time based on priority."""
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

    def classify(self, text: str):
        text_lower = text.lower()

        # Default values
        primary_category = "General"
        sub_category = "General"
        request_type = "Service Request"
        priority_level = "P4 - Low"
        rationale = "Default"
        routing = "Tier 1 Support"
        internal_notes = ""
        is_security = False

        # ================================================================
        # 1. SECURITY OVERRIDE RULES (Highest Precedence)
        # ================================================================
        # IMPORTANT: Check ambiguity FIRST for vague claims, then P1/P2
        # for definitive incidents. This ensures "I think my account was
        # hacked" is treated as ambiguity (P2), not P1.

        # Security Ambiguity (Be Paranoid) — check before definitive keywords
        # to avoid matching "hacked" in "I think my account was hacked"
        if any(k in text_lower for k in self.security_ambiguity_keywords):
            primary_category = "Security & Compliance"
            request_type = "Security / Compliance"
            priority_level = "P2 - High"
            rationale = "Security Ambiguity: Vague security claim detected. Escalating for verification."
            routing = "InfoSec / Tier 2 Security Support"
            internal_notes = ("FLAGGED: Vague security claim. Priority set to High for rapid "
                              "verification by SecOps. Customer should be contacted immediately.")
            is_security = True

        # P1: Critical Security Outage
        elif any(k in text_lower for k in self.p1_security_keywords):
            primary_category = "Security & Compliance"
            request_type = "Security / Compliance"
            priority_level = "P1 - Critical"
            rationale = "Critical Security Outage detected"
            routing = "SecOps & On-Call Manager"
            internal_notes = ("CRITICAL SECURITY OUTAGE: Immediate action required by SecOps. "
                              "All hands on deck. Engage incident response protocol.")
            is_security = True

        # P2: Suspected Security Incident
        elif any(k in text_lower for k in self.p2_security_keywords):
            primary_category = "Security & Compliance"
            request_type = "Security / Compliance"
            priority_level = "P2 - High"
            rationale = "Suspected Security Incident"
            routing = "InfoSec / Tier 2 Security Support"
            internal_notes = ("FLAGGED: Potential security incident. Priority set to High for "
                              "rapid verification by SecOps.")
            is_security = True

        # P3/P4: Routine Security / Access
        elif any(k in text_lower for k in self.security_routine_keywords):
            primary_category = "Security & Compliance"
            request_type = "Authentication & Access"
            # Determine priority from urgency
            urgency_score = self._get_score(text_lower, self.urgency_keywords)
            impact_score = 2  # Security/Access is typically medium impact
            priority_level, rationale = self._determine_priority(impact_score, urgency_score)
            routing = "Identity Support / Tier 1 Support"
            is_security = True

        # ================================================================
        # 2. NON-SECURITY CLASSIFICATION
        # ================================================================
        if not is_security:
            # High-level overrides for Infrastructure/Outage
            high_severity_patterns = [
                r"server\s+(?:is\s+|was\s+)?down",
                r"system\s+(?:is\s+|was\s+)?down",
                r"server.*down",
                r"system.*down",
                r"platform.*down",
                r"outage",
                r"crashed",
                r"crashing",
                r"payment\s+(?:is\s+)?failing",
                r"payment\s+failed",
                r"payment\s+failure",
                r"500\s+error",
                r"error\s+\d{3}",
                r"security\s+breach",
                r"data\s+loss",
                r"complete\s+(?:data\s+)?loss",
                r"all\s+users\s+(?:are\s+|)?locked",
                r"everyone\s+(?:is\s+|)?locked",
            ]
            is_high_severity = any(re.search(p, text_lower) for p in high_severity_patterns)
            is_infrastructure_related = any(
                re.search(p, text_lower)
                for p in [r"server", r"database", r"api\s", r"api\b", r"crash",
                          r"down", r"offline", r"timeout", r"connection\s+refused",
                          r"404", r"500", r"error\s+loading", r"performance",
                          r"slow", r"sandbox", r"platform", r"service"]
            )

            if is_high_severity or is_infrastructure_related:
                primary_category = "Infrastructure & Hosting"
                request_type = "Incident"
                impact_score = 3 if is_high_severity else 2
                urgency_score = 3 if is_high_severity else max(2, self._get_score(text_lower, self.urgency_keywords))
                priority_level, rationale = self._determine_priority(impact_score, urgency_score)


            # Billing-specific check
            elif any(k in text_lower for k in ["billing", "invoice", "invoice's",
                                                 "payment", "charge", "receipt",
                                                 "billing cycle", "refund", "overcharge",
                                                 "subscription", "bill", "charged",
                                                 "incorrect invoice", "last invoice"]):
                primary_category = "Billing"
                request_type = "Billing Inquiry"
                impact_score = 1
                urgency_score = self._get_score(text_lower, self.urgency_keywords)
                # Revenue-impacting billing issues (overcharges, double charges,
                # refunds, billing errors) are elevated so they don't languish as
                # low-priority account questions — these directly affect revenue.
                revenue_impact_keywords = [
                    "overcharge", "over charged", "double charge", "charged twice",
                    "duplicate charge", "incorrect invoice", "wrong charge",
                    "extra charge", "billing error", "charged extra", "missing refund",
                    "refund", "charged", "incorrect",
                ]
                if any(k in text_lower for k in revenue_impact_keywords):
                    impact_score = 2
                    rationale = "Revenue-impacting billing issue detected"
                elif "urgent" in text_lower or "asap" in text_lower or "immediately" in text_lower:
                    impact_score = 2
                priority_level, rationale = self._determine_priority(impact_score, urgency_score)

            # Bug / Defect
            elif any(k in text_lower for k in ["error", "broken", "crash", "failed",

                                                 "bug", "not working", "issue",
                                                 "down", "outage", "slow", "loading",
                                                 "timeout", "connection", "api",
                                                 "server", "database"]):
                primary_category = "Incident"
                request_type = "Bug / Defect"
                impact_score = self._get_score(text_lower, self.impact_keywords)
                urgency_score = self._get_score(text_lower, self.urgency_keywords)
                priority_level, rationale = self._determine_priority(impact_score, urgency_score)

            # Feature Request / Feedback
            elif any(k in text_lower for k in ["add", "new", "feature", "improve",
                                                 "suggestion", "feedback",
                                                 "would be great", "can you"]):
                primary_category = "Feature Request / Feedback"
                request_type = "Feature Request / Feedback"
                impact_score = 1
                urgency_score = 1
                priority_level, rationale = self._determine_priority(impact_score, urgency_score)

            # Service Request
            elif any(k in text_lower for k in ["access", "hardware", "account",
                                                 "update", "configuration",
                                                 "reset", "login", "password"]):
                primary_category = "Service Request"
                request_type = "Service Request"
                impact_score = 1
                urgency_score = self._get_score(text_lower, self.urgency_keywords)
                priority_level, rationale = self._determine_priority(impact_score, urgency_score)

            else:
                # Default fallback
                impact_score = 1
                urgency_score = 1
                priority_level, rationale = self._determine_priority(impact_score, urgency_score)

            # Routing for non-security
            routing = "Tier 1 Support"
            if priority_level == "P1 - Critical":
                routing = "Tier 2 Engineering / DevOps"
            elif priority_level == "P2 - High":
                routing = "Tier 2 Engineering"
            elif "Billing" in primary_category:
                routing = "Billing Department"

        # ================================================================
        # 3. Metrics
        # ================================================================
        metrics = self._extract_metrics(text_lower)

        # ================================================================
        # 4. Response Draft
        # ================================================================
        if is_security:
            if priority_level == "P1 - Critical":
                response = ("We have received your critical report and our engineering team "
                            "has been notified immediately. We will provide an update within 15 minutes.")
            elif priority_level == "P2 - High":
                response = ("We are investigating this high-priority issue. A specialist "
                            "will contact you within the hour.")
            else:
                response = ("Thank you for reaching out. We have logged your request "
                            "and will get back to you within 4 hours.")
        elif is_high_severity and len(text) < 40:
            response = ("We have detected a potential critical outage. Please specify "
                        "which application or environment is affected while our "
                        "Tier 2/DevOps team investigates.")
        elif priority_level == "P1 - Critical":
            response = ("We have received your critical report and our engineering team "
                        "has been notified immediately. We will provide an update within 15 minutes.")
        elif priority_level == "P2 - High":
            response = ("We are investigating this high-priority issue. A specialist "
                        "will contact you within the hour.")
        elif priority_level == "P3 - Medium":
            response = ("Thank you for reaching out. We have logged your request "
                        "and will get back to you within 4 hours.")
        else:
            response = ("Thank you for your message. We will review your request "
                        "and respond within 24 hours.")

        eta = self._calculate_eta(priority_level)

        return {
            "ticket_summary": text[:100] + "..." if len(text) > 100 else text,
            "categorization": {
                "primary_category": primary_category,
                "sub_category": sub_category,
                "request_type": request_type
            },
            "priority": {
                "level": priority_level,
                "rationale": rationale
            },
            "triage": {
                "customer_tier": metrics["tier"],
                "affected_systems": metrics["systems"],
                "reproducibility": metrics["reproducibility"],
                "sentiment": metrics["sentiment"]
            },
            "routing": {
                "suggested_department": routing,
                "internal_notes": internal_notes if internal_notes
                else (f"Priority {priority_level} detected. Sentiment: {metrics['sentiment']}. "
                      f"Systems: {', '.join(metrics['systems'])}.")
            },
            "eta": eta,
            "customer_response_draft": response
        }


# For testing purposes
if __name__ == "__main__":
    classifier = TicketClassifier()
    test_cases = [
        # Security P1
        ("EMERGENCY: Domain administrator account compromised!", "P1"),
        ("ransomware detected on production server", "P1"),
        ("Our production database was hacked!", "P1"),
        # Security P2
        ("phishing email received by multiple users", "P2"),
        ("suspicious login attempt from unknown location", "P2"),
        ("I lost my company laptop", "P2"),
        # Security Ambiguity
        ("I think my account was hacked", "P2"),
        ("someone accessed my files", "P2"),
        # Security Routine
        ("requesting password reset", "P3/P4"),
        ("how to reset mfa", "P3/P4"),
        ("general compliance inquiry regarding SOC2", "P3/P4"),
        # Non-security - Bug/Defect
        ("The system is crashing every time I try to upload a file.", "P2/P3"),
        ("Server is down and I can't access my account!", "P1"),
        # Non-security - Billing
        ("I have a question about my last invoice, it seems incorrect.", "P4"),
        ("Billing error: I was charged twice for my subscription", "P3/P4"),
        # Non-security - Feature Request
        ("Can you add a dark mode feature to the dashboard?", "P4"),
        # Non-security - Service Request
        ("The button color is slightly off on the settings page.", "P4"),
        # Non-security - Infrastructure
        ("The sandbox environment is slow.", "P3/P4"),
        # Non-security - General
        ("Just saying hi.", "P4"),
    ]
    for tc, expected in test_cases:
        print(f"Text: {tc}")
        result = classifier.classify(tc)
        print(json.dumps(result, indent=2))
        print(f"Expected: {expected} | Got: {result['priority']['level']} | "
              f"Category: {result['categorization']['primary_category']}")
        print("-" * 20)
