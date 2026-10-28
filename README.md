# Agentic Procurement-to-Payment (P2P) Control Tower

An autonomous, multi-agent enterprise web platform orchestrating the complete corporate procurement lifecycle from requisition to financial settlement using Python, Flask, SQLite, and LangGraph.

Designed with an enterprise pastel UI system (`#FAF9F6`, `#DCE8F8`, `#E9E1F8`, `#DDF1E5`, `#F7E2E7`, `#F9E9D8`), parameterized SQL queries, integer paise currency precision, strict 3-way matching, and immutable audit logging.

---

## Architecture Overview

The platform coordinates six specialized autonomous agents:

1. **Agent 1: Requirement Analysis (`/agents/requirements`)**
   - Natural language and structured requisition processing.
   - Deterministic rule-based extraction with optional Gemini AI parsing.
   - Enforces positive quantities, budget caps, delivery deadlines, and technical specifications.
2. **Agent 2: Intelligent Vendor Comparison (`/agents/vendors`)**
   - Automatically loads requisition parameters.
   - Evaluates suppliers against mandatory constraints (RAM, storage, delivery SLA, budget cap).
   - Ranks compliant suppliers using L1 multi-factor scoring (cost, rating, warranty).
   - Explains transparent rejection reasons for non-compliant bidders.
3. **Agent 3: Budget and Policy Validation (`/agents/budget-policy`)**
   - Computes total financial commitment with exact paise arithmetic.
   - Evaluates unallocated departmental budgets and remaining balances.
   - Enforces delegation-of-authority policies (>₹50,000 Manager Approval, >₹50 Lakhs Executive VP).
   - Enforces mandatory manager sign-off (passing budget does *not* auto-approve).
4. **Approval Governance Center (`/approvals`)**
   - Dedicated review portal for managers and executives.
   - Role-protected authorization queue with decision comments and audit trails.
5. **Agent 4: Purchase Order Issuance (`/agents/purchase-orders`)**
   - Verifies prior manager authorization.
   - Generates tamper-evident unique PO numbers (`PO-YYYYMMDD-XXXX`).
   - Prevents duplicate PO issuance.
   - Automatically synchronizes Goods Receipt Notes (GRN) with warehouse ERP systems.
   - Provides printable and downloadable PDF purchase orders.
6. **Agent 5: 3-Way Invoice Matching (`/agents/invoice-matching`)**
   - Extracts supplier PDF and image invoices using OCR and PDF text parsing.
   - Reconciles Purchase Order, Supplier Invoice, and Warehouse Goods Receipt Note.
   - Enforces 0% unauthorized price and quantity variance.
   - Blocks mismatched or overbilled invoices from payment.
7. **Agent 6: Controlled Payment Settlement (`/agents/approval-payment`)**
   - Validates 3-way match certification and manager sign-off.
   - Enforces idempotency keys (`IDEM-...`) to prevent double-spending.
   - Simulates enterprise RTGS/NEFT payment gateway disbursement.
   - Records verifiable transaction references (`TXN-HDFC-RTGS-...`).
8. **Audit Trail & Telemetry (`/audit-logs`)**
   - Chronological, immutable lifecycle log of all agent decisions, user actions, and settlements.

---

## Technology Stack

- **Backend**: Python 3.12+, Flask
- **Multi-Agent Orchestration**: LangGraph
- **Database**: SQLite (13 tables, parameterized queries, integer paise monetary storage)
- **Security**: Werkzeug password hashing, session-based CSRF protection, role authorization
- **Document Processing**: `pypdf`, `reportlab`
- **Frontend**: HTML5, Vanilla CSS3 (Curated Pastel Palette), Jinja2 templates

---

## Quick Start (Windows)

### 1. Environment Configuration
The `.env` file is already created in the project root:
```ini
SECRET_KEY=procurement-control-tower-secret-key-2026-secure
GEMINI_API_KEY=
PORT=5000
DEBUG=True
```
*Note: If `GEMINI_API_KEY` is provided, natural language purchase briefs can leverage Gemini AI. If empty, the system runs in deterministic rule-based mode.*

### 2. Run the Application
The entire application logic is contained in `app.py`:
```powershell
python app.py
```
Open your browser at **http://127.0.0.1:5000**.

### 3. Pre-seeded Test Accounts
The SQLite database automatically initializes with test accounts on first launch:

| Role | Email | Password | Privileges |
| :--- | :--- | :--- | :--- |
| **Manager** | `manager@procurecorp.com` | `Manager@123` | Can approve/reject requisitions & sign off payments |
| **Employee** | `employee@procurecorp.com` | `Employee@123` | Can submit requisitions, compare vendors, upload invoices |
| **Admin** | `admin@procurecorp.com` | `Admin@123` | Full access across all departments and governance controls |

---

## Running the Automated Test Suite

To run the automated `pytest` test suite:
```powershell
python -m pytest -v tests/test_all.py
```
All 8 test suites cover:
- Public registration, credential validation, and duplicate prevention
- Agent 1 natural language parsing and structured requisition persistence
- Agent 2 multi-criteria vendor filtering and transparent rejection explanations
- Agent 3 budget checks and spend threshold policies
- Manager approval gates and duplicate PO prevention
- Agent 5 3-way invoice matching (both match and variance detection)
- Agent 6 payment idempotency and duplicate disbursement prevention

---

## File Structure

```
Procurement/
├── .env                  # Environment configuration & GEMINI_API_KEY
├── app.py                # Complete application code (Agents, DB, LangGraph, Routes)
├── procurement.db        # SQLite persistent database (created automatically)
├── requirements.txt      # Dependency specification
├── README.md             # Project documentation
│
├── templates/
│   ├── base.html         # Sidebar, navigation, user info, alerts
│   ├── home.html         # Hero, features, how it works, benefits
│   ├── signin.html       # Authentication & demo account auto-filler
│   ├── signup.html       # Public registration with employee role default
│   ├── dashboard.html    # 7 KPI stat cards, pipeline status, audit stream
│   ├── agent_requirements.html   # Agent 1 Requisition input & brief parser
│   ├── agent_vendors.html        # Agent 2 Supplier comparison matrix
│   ├── agent_budget_policy.html  # Agent 3 Fiscal validation & policy gates
│   ├── agent_purchase_order.html # Agent 4 Official PO generator
│   ├── agent_invoice_matching.html# Agent 5 3-Way matching & OCR
│   ├── agent_approval_payment.html# Agent 6 Payment settlement & idempotency
│   ├── approvals.html    # Governance approval center for managers
│   ├── audit_logs.html   # Complete system audit trail
│   └── po_view.html      # Printable official Purchase Order document
│
├── static/
│   └── css/
│       └── style.css     # Pastel UI design system
│
├── uploads/
│   └── samples/          # Pre-generated sample invoices for testing
│
└── tests/
    └── test_all.py       # Automated pytest test suite
```
