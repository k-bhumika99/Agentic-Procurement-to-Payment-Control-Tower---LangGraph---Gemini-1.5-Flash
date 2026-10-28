import os
import sys
import re
import json
import uuid
import secrets
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, TypedDict

# Load .env file
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Third-party imports
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file, jsonify, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from langgraph.graph import StateGraph, END

# Optional Gemini LLM import
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
gemini_model = None
if GEMINI_KEY:
    try:
        # pyrefly: ignore[missing-import]
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_KEY)
        gemini_model = genai.GenerativeModel("gemini-1.5-flash")
    except Exception as e:
        print(f"Notice: Gemini initialization skipped or unavailable: {e}")

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DATABASE_PATH = str(BASE_DIR / "procurement.db")
UPLOAD_FOLDER = str(BASE_DIR / "uploads")
SAMPLES_FOLDER = str(BASE_DIR / "uploads" / "samples")
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}

Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
Path(SAMPLES_FOLDER).mkdir(parents=True, exist_ok=True)

# ==============================================================================
# DATABASE LAYER & MONEY HELPERS (INTEGER PAISE ARITHMETIC)
# ==============================================================================
def get_db_connection(db_path=None):
    if db_path is None:
        try:
            db_path = app.config.get("DATABASE_PATH", DATABASE_PATH)
        except Exception:
            db_path = DATABASE_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def paise_to_rupees(paise: int) -> float:
    return (paise or 0) / 100.0

def rupees_to_paise(rupees: float) -> int:
    return int(round((rupees or 0.0) * 100))

def format_inr(paise: int) -> str:
    """Format integer paise into Indian numbering system currency string (e.g. ₹2,50,00,000.00)."""
    if paise is None:
        paise = 0
    rupees = paise / 100.0
    is_negative = rupees < 0
    rupees = abs(rupees)
    
    parts = f"{rupees:.2f}".split(".")
    integer_part = parts[0]
    decimal_part = parts[1]
    
    if len(integer_part) <= 3:
        formatted = integer_part
    else:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        groups = []
        while remaining:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        formatted = ",".join(groups) + "," + last_three
        
    res = f"₹{formatted}.{decimal_part}"
    return f"-{res}" if is_negative else res

def format_inr_short(paise: int) -> str:
    if paise is None:
        return "₹0"
    rupees = paise / 100.0
    if abs(rupees) >= 10000000:
        return f"₹{rupees / 10000000:.2f} Cr"
    elif abs(rupees) >= 100000:
        return f"₹{rupees / 100000:.2f} L"
    else:
        return format_inr(paise)

def init_db(db_path=None):
    if db_path is None:
        db_path = DATABASE_PATH
    conn = get_db_connection(db_path)
    cur = conn.cursor()

    # 1. Users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        department TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('employee', 'manager', 'admin')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Purchase Requests
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_requests (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        user_id INTEGER,
        department TEXT NOT NULL,
        item_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        specs TEXT NOT NULL,
        min_ram_gb INTEGER DEFAULT 0,
        min_storage_gb INTEGER DEFAULT 0,
        max_budget_paise INTEGER NOT NULL,
        delivery_deadline_days INTEGER NOT NULL,
        available_budget_paise INTEGER DEFAULT 0,
        additional_requirements TEXT,
        raw_prompt TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        selected_vendor_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (selected_vendor_id) REFERENCES vendors(id)
    );
    """)

    # 3. Vendors
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vendors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        product_name TEXT NOT NULL,
        product_specs TEXT NOT NULL,
        ram_gb INTEGER DEFAULT 0,
        storage_gb INTEGER DEFAULT 0,
        unit_price_paise INTEGER NOT NULL,
        delivery_days INTEGER NOT NULL,
        warranty_years INTEGER NOT NULL,
        rating REAL NOT NULL CHECK(rating >= 1.0 AND rating <= 5.0),
        contact_email TEXT,
        is_active INTEGER DEFAULT 1
    );
    """)

    # 4. Department Budgets
    cur.execute("""
    CREATE TABLE IF NOT EXISTS department_budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department TEXT UNIQUE NOT NULL,
        total_budget_paise INTEGER NOT NULL,
        allocated_paise INTEGER NOT NULL DEFAULT 0,
        fiscal_year TEXT NOT NULL
    );
    """)

    # 5. Company Policies
    cur.execute("""
    CREATE TABLE IF NOT EXISTS company_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_key TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        threshold_paise INTEGER NOT NULL,
        description TEXT NOT NULL,
        requires_manager_approval INTEGER DEFAULT 1,
        requires_vp_approval INTEGER DEFAULT 0
    );
    """)

    # 6. Purchase Orders
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id TEXT PRIMARY KEY,
        pr_id TEXT NOT NULL,
        vendor_id INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price_paise INTEGER NOT NULL,
        total_amount_paise INTEGER NOT NULL,
        delivery_terms TEXT NOT NULL,
        payment_terms TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'issued',
        created_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (pr_id) REFERENCES purchase_requests(id),
        FOREIGN KEY (vendor_id) REFERENCES vendors(id),
        FOREIGN KEY (created_by) REFERENCES users(id)
    );
    """)

    # 7. Goods Receipts
    cur.execute("""
    CREATE TABLE IF NOT EXISTS goods_receipts (
        id TEXT PRIMARY KEY,
        po_id TEXT NOT NULL,
        item_name TEXT NOT NULL,
        quantity_ordered INTEGER NOT NULL,
        quantity_received INTEGER NOT NULL,
        inspection_status TEXT NOT NULL CHECK(inspection_status IN ('accepted', 'rejected', 'partial')),
        notes TEXT,
        received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id)
    );
    """)

    # 8. Invoices
    cur.execute("""
    CREATE TABLE IF NOT EXISTS invoices (
        id TEXT PRIMARY KEY,
        po_id TEXT NOT NULL,
        vendor_name TEXT NOT NULL,
        invoice_number TEXT NOT NULL,
        invoice_date TEXT NOT NULL,
        item_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price_paise INTEGER NOT NULL,
        total_amount_paise INTEGER NOT NULL,
        file_path TEXT,
        original_filename TEXT,
        match_status TEXT NOT NULL DEFAULT 'pending',
        match_details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id)
    );
    """)

    # 9. Purchase Approvals
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pr_id TEXT NOT NULL,
        approver_id INTEGER NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected')),
        comment TEXT,
        decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (pr_id) REFERENCES purchase_requests(id),
        FOREIGN KEY (approver_id) REFERENCES users(id)
    );
    """)

    # 10. Payment Approvals
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id TEXT NOT NULL,
        pr_id TEXT NOT NULL,
        approver_id INTEGER NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected')),
        comment TEXT,
        decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES invoices(id),
        FOREIGN KEY (pr_id) REFERENCES purchase_requests(id),
        FOREIGN KEY (approver_id) REFERENCES users(id)
    );
    """)

    # 11. Payments
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id TEXT PRIMARY KEY,
        invoice_id TEXT NOT NULL,
        po_id TEXT NOT NULL,
        pr_id TEXT NOT NULL,
        amount_paise INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('completed', 'failed', 'pending')),
        transaction_reference TEXT UNIQUE,
        idempotency_key TEXT UNIQUE,
        failure_reason TEXT,
        payment_method TEXT DEFAULT 'Online',
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (invoice_id) REFERENCES invoices(id),
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id),
        FOREIGN KEY (pr_id) REFERENCES purchase_requests(id)
    );
    """)

    # 12. Audit Logs
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT,
        action TEXT NOT NULL,
        actor TEXT NOT NULL,
        stage TEXT NOT NULL,
        details TEXT,
        ip_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 13. Workflow Checkpoints
    cur.execute("""
    CREATE TABLE IF NOT EXISTS workflow_checkpoints (
        request_id TEXT PRIMARY KEY,
        current_node TEXT NOT NULL,
        state_json TEXT NOT NULL,
        is_paused INTEGER DEFAULT 0,
        pause_reason TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    seed_db(conn)
    conn.close()

def get_category_for_item(item_name: str) -> str:
    low = (item_name or "").lower()
    if any(w in low for w in ["watch", "wearable", "band", "smartwatch", "fitness", "smart watch"]):
        return "Smart Watches & Wearables"
    elif any(w in low for w in ["laptop", "notebook", "thinkbook", "macbook", "pc", "computer"]):
        return "Laptops & Hardware"
    elif any(w in low for w in ["chair", "furniture", "desk", "table", "workstation", "seating"]):
        return "Office Furniture"
    elif any(w in low for w in ["monitor", "display", "screen"]):
        return "Monitors & Displays"
    elif any(w in low for w in ["printer", "scanner", "copier"]):
        return "Printers & Imaging"
    else:
        return item_name.strip().title()

def ensure_vendors_for_category(conn, category: str, item_name: str, quantity: int, max_budget_paise: int):
    cur = conn.cursor()
    cur.execute("SELECT id, name, unit_price_paise FROM vendors WHERE category = ? AND is_active = 1", (category,))
    vendors = cur.fetchall()

    if max_budget_paise > 0 and quantity > 0:
        target_unit_paise = max_budget_paise // max(quantity, 1)
        # Factors calibrated so lowest recommended vendor uses 75% to 80% (0.77) and is less than other vendors (0.83, 0.87, 0.93)
        factors = [0.77, 0.83, 0.87, 0.93, 1.06]

        if not vendors:
            clean_item = item_name.strip()
            clean_cat = category.strip()
            synthetic_vendors = [
                (f"Titan {clean_item} Direct", clean_cat, f"Titan Pro {clean_item} Series-X",
                 f"Enterprise Grade {clean_item}, High Reliability, 3-Year Corporate SLA",
                 0, 0, int(target_unit_paise * 0.77), 4, 3, 4.8, "orders@titan-direct.com"),
                (f"Samsung {clean_item} B2B", clean_cat, f"Samsung Enterprise {clean_item}",
                 f"Premium Business {clean_item}, Energy Efficient, Extended Battery",
                 0, 0, int(target_unit_paise * 0.83), 5, 2, 4.7, "b2b@samsung-solutions.in"),
                (f"Noise Corporate Solutions", clean_cat, f"Noise Core {clean_item} Commercial",
                 f"Enterprise Standard {clean_item}, Advanced Sensor Suite",
                 0, 0, int(target_unit_paise * 0.87), 3, 2, 4.6, "enterprise@noise-b2b.com"),
                (f"Boat Enterprise Sourcing", clean_cat, f"Boat Commercial {clean_item}",
                 f"Reliable Corporate {clean_item}, Standard Warranty",
                 0, 0, int(target_unit_paise * 0.93), 6, 1, 4.3, "support@boat-enterprise.com"),
                (f"Vanguard Global Trade", clean_cat, f"Vanguard Master {clean_item}",
                 f"Flagship Tier {clean_item}, Aerospace Grade Materials",
                 0, 0, int(target_unit_paise * 1.06), 14, 3, 4.9, "b2b@vanguardtrade.com")
            ]
            cur.executemany("""
                INSERT INTO vendors (name, category, product_name, product_specs, ram_gb, storage_gb, unit_price_paise, delivery_days, warranty_years, rating, contact_email)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, synthetic_vendors)
            conn.commit()
        else:
            # Calibrate existing vendors in this category so quotes scale to the PR budget scope
            for idx, v in enumerate(vendors):
                factor = factors[idx % len(factors)]
                new_price = int(target_unit_paise * factor)
                cur.execute("UPDATE vendors SET unit_price_paise = ? WHERE id = ?", (new_price, v[0]))
            conn.commit()

def seed_db(conn):
    cur = conn.cursor()

    # Seed Default Users
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        users = [
            ("Admin Executive", "admin@procurecorp.com", generate_password_hash("Admin@123"), "Executive", "admin"),
            ("Anita Sharma (Manager)", "manager@procurecorp.com", generate_password_hash("Manager@123"), "IT", "manager"),
            ("Rajesh Patel (Employee)", "employee@procurecorp.com", generate_password_hash("Employee@123"), "IT", "employee")
        ]
        cur.executemany("INSERT INTO users (full_name, email, password_hash, department, role) VALUES (?, ?, ?, ?, ?)", users)

    # 1. Seed Laptops & Hardware
    cur.execute("SELECT COUNT(*) FROM vendors WHERE category = 'Laptops & Hardware'")
    if cur.fetchone()[0] == 0:
        laptop_vendors = [
            ("TechNova Solutions", "Laptops & Hardware", "TechNova ThinkBook Pro",
             "Intel Core i7 13th Gen, 16 GB DDR5 RAM, 512 GB NVMe SSD, 14-inch FHD IPS",
             16, 512, 4800000, 7, 3, 4.8, "sales@technova-solutions.com"),
            ("Apex Digital Corp", "Laptops & Hardware", "Apex EliteBook 840 G10",
             "Intel Core i5 13th Gen, 16 GB DDR5 RAM, 512 GB SSD, 14-inch FHD",
             16, 512, 4700000, 14, 2, 4.3, "enterprise@apexdigital.com"),
            ("Zenith Systems India", "Laptops & Hardware", "Zenith OfficeBook Lite",
             "Intel Core i3 12th Gen, 8 GB RAM, 256 GB SSD, 15.6-inch HD",
             8, 256, 3800000, 5, 1, 4.0, "support@zenithindia.com"),
            ("PrimeByte Technologies", "Laptops & Hardware", "Prime Latitude Workstation",
             "Intel Core i7 13th Gen, 16 GB RAM, 512 GB SSD, 14-inch FHD Anti-Glare",
             16, 512, 4900000, 9, 3, 4.6, "b2b@primebyte.com"),
            ("CloudVanguard Infotech", "Laptops & Hardware", "Vanguard Developer Pro Max",
             "Apple M3 Pro / 32 GB Unified Memory, 1 TB SSD, 14-inch Liquid Retina",
             32, 1024, 8500000, 8, 3, 4.9, "enterprise@cloudvanguard.com")
        ]
        cur.executemany("""
            INSERT INTO vendors (name, category, product_name, product_specs, ram_gb, storage_gb, unit_price_paise, delivery_days, warranty_years, rating, contact_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, laptop_vendors)

    # 2. Seed Smart Watches & Wearables
    cur.execute("SELECT COUNT(*) FROM vendors WHERE category = 'Smart Watches & Wearables'")
    if cur.fetchone()[0] == 0:
        watch_vendors = [
            ("Titan Corporate Solutions", "Smart Watches & Wearables", "Titan Smart Pro Touch GPS",
             "1.43\" AMOLED Display, Optical Heart Rate & SpO2, 14-Day Battery, 5ATM Water Resistant",
             0, 0, 350000, 4, 2, 4.8, "corporate@titan.co.in"),
            ("Boat Enterprise Direct", "Smart Watches & Wearables", "Boat Wave Call Enterprise",
             "1.83\" HD Bright Display, Bluetooth Calling, 100+ Sports Modes, IP68 Waterproof",
             0, 0, 220000, 5, 1, 4.4, "b2b@boat-lifestyle.com"),
            ("Noise B2B Systems", "Smart Watches & Wearables", "Noise ColorFit Pulse 2 Max",
             "1.85\" TFT LCD, 10-Day Battery, Noise Health Suite, Scratch-resistant Glass",
             0, 0, 280000, 3, 2, 4.6, "enterprise@gonoise.com"),
            ("Samsung Business Solutions", "Smart Watches & Wearables", "Samsung Galaxy Fit3 Enterprise Edition",
             "Aluminum Case, Fall Detection, Emergency SOS, 13-Day Battery, Sleep Coach",
             0, 0, 450000, 6, 2, 4.9, "b2b.india@samsung.com"),
            ("Fire-Boltt Commercial", "Smart Watches & Wearables", "Fire-Boltt Invincible Plus",
             "1.43\" AMOLED Display, 4GB Local Storage, TWS Earphone Connection, AI Voice Assistant",
             0, 0, 420000, 12, 1, 4.2, "commercial@fireboltt.com")
        ]
        cur.executemany("""
            INSERT INTO vendors (name, category, product_name, product_specs, ram_gb, storage_gb, unit_price_paise, delivery_days, warranty_years, rating, contact_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, watch_vendors)

    # 3. Seed Office Furniture & Chairs
    cur.execute("SELECT COUNT(*) FROM vendors WHERE category = 'Office Furniture'")
    if cur.fetchone()[0] == 0:
        chair_vendors = [
            ("Featherlite Workspaces", "Office Furniture", "Featherlite Helix Ergonomic Mesh Chair",
             "Adjustable Lumbar Support, 3D Armrests, Breathable Mesh Back, Class 4 Gas Lift",
             0, 0, 850000, 5, 3, 4.8, "sales@featherlite.com"),
            ("Godrej Interio Corporate", "Office Furniture", "Godrej Motion High-Back Executive Chair",
             "Synchro-tilt Multi-lock Mechanism, High-density Molded Foam, Nylon Castors",
             0, 0, 1100000, 7, 3, 4.7, "b2b@godrej.com"),
            ("Green Soul Ergonomics", "Office Furniture", "Green Soul Monster Ultimate Chair",
             "Breathable Spandex Fabric, Magnetic Neck Pillow, Heavy-duty Metal Base",
             0, 0, 980000, 4, 2, 4.5, "corporate@greensoul.online"),
            ("Wipro Furniture Solutions", "Office Furniture", "Wipro Adapt High-Back Chair",
             "Ergonomic Contoured Mesh, Pneumatic Height Adjustment, Anti-skid Base",
             0, 0, 790000, 6, 2, 4.4, "b2b@wipro.com")
        ]
        cur.executemany("""
            INSERT INTO vendors (name, category, product_name, product_specs, ram_gb, storage_gb, unit_price_paise, delivery_days, warranty_years, rating, contact_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, chair_vendors)

    # Seed Department Budgets (IT = ₹5 Crore)
    cur.execute("SELECT COUNT(*) FROM department_budgets")
    if cur.fetchone()[0] == 0:
        budgets = [
            ("IT", 5000000000, 0, "FY 2026-27"),
            ("Operations", 2000000000, 0, "FY 2026-27"),
            ("Human Resources", 500000000, 0, "FY 2026-27"),
            ("Finance", 1000000000, 0, "FY 2026-27"),
            ("Marketing", 800000000, 0, "FY 2026-27")
        ]
        cur.executemany("INSERT INTO department_budgets (department, total_budget_paise, allocated_paise, fiscal_year) VALUES (?, ?, ?, ?)", budgets)

    # Seed Company Policies
    cur.execute("SELECT COUNT(*) FROM company_policies")
    if cur.fetchone()[0] == 0:
        policies = [
            ("TIER_1_STANDARD", "Standard Low-Value Purchase", 5000000, "Purchases under ₹50,000 do not require executive approval.", 0, 0),
            ("TIER_2_MANAGER", "Manager Approval Threshold", 5000000, "Purchases between ₹50,000 and ₹50 Lakhs require Department Manager approval.", 1, 0),
            ("TIER_3_EXECUTIVE", "Executive Board Approval Threshold", 500000000, "Purchases exceeding ₹50 Lakhs require Executive / VP approval.", 1, 1),
            ("THREE_WAY_MATCH", "Strict 3-Way Matching Policy", 0, "Invoice, PO, and GRN quantities & prices must match with 0% unauthorized variance.", 1, 0)
        ]
        cur.executemany("""
            INSERT INTO company_policies (policy_key, name, threshold_paise, description, requires_manager_approval, requires_vp_approval)
            VALUES (?, ?, ?, ?, ?, ?)
        """, policies)

    conn.commit()

# ==============================================================================
# AUDIT SERVICE
# ==============================================================================
class AuditService:
    @staticmethod
    def log_event(action: str, stage: str, actor: str, request_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None, ip_address: Optional[str] = "127.0.0.1", db_path=None):
        conn = get_db_connection(db_path)
        details_str = json.dumps(details) if isinstance(details, dict) else str(details or "")
        conn.execute("""
            INSERT INTO audit_logs (request_id, action, actor, stage, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (request_id, action, actor, stage, details_str, ip_address))
        conn.commit()
        conn.close()

    @staticmethod
    def get_recent(limit: int = 50, db_path=None) -> List[Dict[str, Any]]:
        conn = get_db_connection(db_path)
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_for_request(request_id: str, db_path=None) -> List[Dict[str, Any]]:
        conn = get_db_connection(db_path)
        rows = conn.execute("SELECT * FROM audit_logs WHERE request_id = ? ORDER BY created_at DESC", (request_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

# ==============================================================================
# ERP & PAYMENT SIMULATION SERVICES
# ==============================================================================
class ERPService:
    @staticmethod
    def create_goods_receipt(po_id: str, item_name: str, quantity_ordered: int, quantity_received: int, inspection_status: str = "accepted", notes: str = "Automated warehouse scanner verification passed.", db_path=None) -> str:
        grn_id = f"GRN-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        conn = get_db_connection(db_path)
        conn.execute("""
            INSERT INTO goods_receipts (id, po_id, item_name, quantity_ordered, quantity_received, inspection_status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (grn_id, po_id, item_name, quantity_ordered, quantity_received, inspection_status, notes))
        conn.commit()
        conn.close()
        return grn_id

    @staticmethod
    def get_goods_receipt(po_id: str, db_path=None) -> Optional[Dict[str, Any]]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM goods_receipts WHERE po_id = ? ORDER BY received_at DESC", (po_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

class PaymentService:
    @staticmethod
    def process_payment(invoice_id: str, po_id: str, pr_id: str, amount_paise: int, idempotency_key: str, payment_method: str = "Online", simulate_failure: bool = False, db_path=None) -> Dict[str, Any]:
        conn = get_db_connection(db_path)

        # Clear any failed payment attempts for this invoice so user can retry successfully
        conn.execute("DELETE FROM payments WHERE invoice_id = ? AND status = 'failed'", (invoice_id,))
        conn.commit()

        # Prevent duplicate payment on same invoice if already settled
        settled_inv = conn.execute("SELECT * FROM payments WHERE invoice_id = ? AND status = 'completed'", (invoice_id,)).fetchone()
        if settled_inv:
            conn.close()
            return {
                "success": True,
                "payment_id": settled_inv["id"],
                "status": "completed",
                "payment_method": settled_inv["payment_method"] if "payment_method" in settled_inv.keys() else "Online",
                "transaction_reference": settled_inv["transaction_reference"],
                "message": f"Invoice {invoice_id} is already settled under transaction {settled_inv['transaction_reference']}.",
                "duplicate": True
            }

        payment_id = f"PAY-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        if simulate_failure:
            fail_reason = "Simulated bank gateway timeout or liquidity hold."
            conn.execute("""
                INSERT INTO payments (id, invoice_id, po_id, pr_id, amount_paise, status, transaction_reference, idempotency_key, failure_reason, payment_method)
                VALUES (?, ?, ?, ?, ?, 'failed', NULL, ?, ?, ?)
            """, (payment_id, invoice_id, po_id, pr_id, amount_paise, idempotency_key, fail_reason, payment_method))
            conn.commit()
            conn.close()
            return {
                "success": False,
                "payment_id": payment_id,
                "status": "failed",
                "transaction_reference": None,
                "message": fail_reason
            }

        if payment_method.lower() == "cash":
            txn_ref = f"RCPT-CASH-DESK-{datetime.now().strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6].upper()}"
            msg = "Disbursement confirmed via Corporate Cash Desk voucher."
        else:
            payment_method = "Online"
            txn_ref = f"TXN-ONLINE-RTGS-{datetime.now().strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6].upper()}"
            msg = "Payment executed successfully through Corporate Online Banking (RTGS)."

        conn.execute("""
            INSERT INTO payments (id, invoice_id, po_id, pr_id, amount_paise, status, transaction_reference, idempotency_key, failure_reason, payment_method)
            VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, NULL, ?)
        """, (payment_id, invoice_id, po_id, pr_id, amount_paise, txn_ref, idempotency_key, payment_method))

        conn.execute("UPDATE purchase_orders SET status = 'closed' WHERE id = ?", (po_id,))
        conn.execute("UPDATE purchase_requests SET status = 'payment_completed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (pr_id,))
        conn.commit()
        conn.close()

        return {
            "success": True,
            "payment_id": payment_id,
            "status": "completed",
            "payment_method": payment_method,
            "transaction_reference": txn_ref,
            "message": msg
        }

# ==============================================================================
# OCR & INVOICE PARSING SERVICE
# ==============================================================================
class OCRService:
    @classmethod
    def extract_text(cls, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            return ""
        ext = path.suffix.lower()
        extracted = ""
        if ext == ".pdf":
            try:
                reader = PdfReader(str(path))
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        extracted += text + "\n"
            except Exception as e:
                extracted = f"Error reading PDF: {e}"
        elif ext in [".png", ".jpg", ".jpeg"]:
            try:
                import pytesseract
                from PIL import Image
                img = Image.open(str(path))
                extracted = pytesseract.image_to_string(img)
            except Exception as e:
                extracted = f"OCR raw read error: {e}"
        return extracted.strip()

    @classmethod
    def parse_invoice_text(cls, text: str) -> Dict[str, Any]:
        fields = {
            "invoice_number": None,
            "po_id": None,
            "vendor_name": None,
            "item_name": None,
            "quantity": None,
            "unit_price_paise": None,
            "total_amount_paise": None,
            "invoice_date": None,
            "raw_text": text
        }

        # 1. Invoice Number
        inv_match = re.search(r"(?:invoice\s*(?:no|number|#)?[:\s]*)([A-Z0-9\-]+)", text, re.IGNORECASE)
        if inv_match:
            fields["invoice_number"] = inv_match.group(1).strip()
        else:
            gen_inv = re.search(r"\b(INV-[A-Z0-9\-]+)\b", text, re.IGNORECASE)
            if gen_inv:
                fields["invoice_number"] = gen_inv.group(1).strip()

        # 2. PO Number
        po_match = re.search(r"(?:po\s*(?:no|number|#|ref)?[:\s]*)([A-Z0-9\-]+)", text, re.IGNORECASE)
        if po_match:
            fields["po_id"] = po_match.group(1).strip()
        else:
            gen_po = re.search(r"\b(PO-[A-Z0-9\-]+)\b", text, re.IGNORECASE)
            if gen_po:
                fields["po_id"] = gen_po.group(1).strip()

        # 3. Vendor Name
        vendor_match = re.search(r"(?:vendor|supplier|from|seller)[:\s]*([^\n\r]+)", text, re.IGNORECASE)
        if vendor_match:
            fields["vendor_name"] = vendor_match.group(1).strip()
        else:
            for kv in ["TechNova Solutions", "Apex Digital Corp", "Zenith Systems India", "PrimeByte Technologies", "CloudVanguard Infotech"]:
                if kv.lower() in text.lower():
                    fields["vendor_name"] = kv
                    break

        # 4. Quantity
        qty_match = re.search(r"(?:quantity|qty)[:\s]*([0-9,]+)", text, re.IGNORECASE)
        if qty_match:
            try:
                fields["quantity"] = int(qty_match.group(1).replace(",", "").strip())
            except ValueError:
                pass

        # 5. Unit Price
        unit_match = re.search(r"(?:unit\s*price|rate|price/unit)[:\s]*[₹Rs\.]*\s*([0-9,]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
        if unit_match:
            try:
                fields["unit_price_paise"] = rupees_to_paise(float(unit_match.group(1).replace(",", "").strip()))
            except ValueError:
                pass

        # 6. Total Amount
        total_match = re.search(r"(?:total\s*(?:amount)?|grand\s*total|invoice\s*total)[:\s]*[₹Rs\.]*\s*([0-9,]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
        if total_match:
            try:
                fields["total_amount_paise"] = rupees_to_paise(float(total_match.group(1).replace(",", "").strip()))
            except ValueError:
                pass

        # If quantity and unit price exist but total doesn't
        if fields["quantity"] and fields["unit_price_paise"] and not fields["total_amount_paise"]:
            fields["total_amount_paise"] = fields["quantity"] * fields["unit_price_paise"]

        return fields

# ==============================================================================
# SAMPLE INVOICE PDF GENERATOR
# ==============================================================================
def generate_sample_invoice_pdf(filename: str, po_id: str, inv_number: str, vendor_name: str, item_name: str, quantity: int, unit_price_inr: float):
    file_path = os.path.join(SAMPLES_FOLDER, filename)
    c = canvas.Canvas(file_path, pagesize=letter)
    total_inr = quantity * unit_price_inr

    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, 750, f"TAX INVOICE - {vendor_name.upper()}")

    c.setFont("Helvetica", 11)
    c.drawString(50, 725, f"Invoice #: {inv_number}")
    c.drawString(50, 710, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    c.drawString(50, 695, f"PO Reference: {po_id}")
    c.drawString(50, 680, f"Supplier: {vendor_name}")

    c.line(50, 665, 550, 665)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, 645, "Description")
    c.drawString(300, 645, "Qty")
    c.drawString(370, 645, "Unit Price")
    c.drawString(460, 645, "Total (INR)")
    c.line(50, 635, 550, 635)

    c.setFont("Helvetica", 10)
    c.drawString(50, 615, f"{item_name} - Enterprise Commercial Delivery")
    c.drawString(300, 615, f"Qty: {quantity}")
    c.drawString(370, 615, f"Rate: {unit_price_inr:,.2f}")
    c.drawString(460, 615, f"{total_inr:,.2f}")

    c.line(50, 595, 550, 595)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(350, 575, "Grand Total:")
    c.drawString(460, 575, f"Total Amount: {total_inr:,.2f}")

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(50, 530, "Terms: Payment due within 30 days upon acceptance of goods.")
    c.drawString(50, 515, "Subject to 3-way reconciliation against buyer Purchase Order and Goods Receipt.")
    c.save()
    return file_path

# ==============================================================================
# SIX AUTONOMOUS AGENTS
# ==============================================================================

# Agent 1: Requirement Analysis
class RequirementAgent:
    @classmethod
    def parse_natural_language(cls, text: str) -> Dict[str, Any]:
        extracted = {
            "item_name": "Enterprise Laptops",
            "quantity": 500,
            "min_ram_gb": 16,
            "min_storage_gb": 512,
            "delivery_deadline_days": 10,
            "max_budget_paise": 2500000000,
            "specs": text,
            "additional_requirements": ""
        }

        # Try Gemini if key is provided
        if gemini_model:
            try:
                prompt = (
                    f"Extract procurement parameters from this text as JSON with keys: "
                    f"item_name (string), quantity (int), min_ram_gb (int), min_storage_gb (int), "
                    f"delivery_deadline_days (int), max_budget_rupees (float). Text: '{text}'"
                )
                response = gemini_model.generate_content(prompt)
                raw_json = re.search(r"\{.*\}", response.text, re.DOTALL)
                if raw_json:
                    parsed = json.loads(raw_json.group(0))
                    if parsed.get("quantity"): extracted["quantity"] = int(parsed["quantity"])
                    if parsed.get("min_ram_gb"): extracted["min_ram_gb"] = int(parsed["min_ram_gb"])
                    if parsed.get("min_storage_gb"): extracted["min_storage_gb"] = int(parsed["min_storage_gb"])
                    if parsed.get("delivery_deadline_days"): extracted["delivery_deadline_days"] = int(parsed["delivery_deadline_days"])
                    if parsed.get("max_budget_rupees"): extracted["max_budget_paise"] = rupees_to_paise(float(parsed["max_budget_rupees"]))
                    if parsed.get("item_name"): extracted["item_name"] = str(parsed["item_name"])
                    return extracted
            except Exception as e:
                print(f"Gemini fallback to regex: {e}")

        # Deterministic Regex Engine
        qty_match = re.search(r"(\d+)\s*(?:laptops?|units?|items?|computers?|notebooks?|machines?|pcs?)", text, re.IGNORECASE)
        if qty_match: extracted["quantity"] = int(qty_match.group(1))

        # RAM extraction (explicitly look for RAM / Memory)
        ram_match = re.search(r"(\d+)\s*GB\s*(?:RAM|Memory)", text, re.IGNORECASE)
        if ram_match:
            extracted["min_ram_gb"] = int(ram_match.group(1))

        # Storage extraction (explicitly look for SSD / Storage / NVMe / Disk)
        tb_match = re.search(r"(\d+)\s*TB\s*(?:SSD|Storage|NVMe|Disk)", text, re.IGNORECASE)
        if tb_match:
            extracted["min_storage_gb"] = int(tb_match.group(1)) * 1024
        else:
            storage_match = re.search(r"(\d+)\s*GB\s*(?:SSD|Storage|NVMe|Disk)", text, re.IGNORECASE)
            if storage_match:
                extracted["min_storage_gb"] = int(storage_match.group(1))

        # Fallback if RAM not matched yet
        if not ram_match:
            ram_match_gen = re.search(r"(\d+)\s*GB", text, re.IGNORECASE)
            if ram_match_gen and int(ram_match_gen.group(1)) != extracted.get("min_storage_gb"):
                extracted["min_ram_gb"] = int(ram_match_gen.group(1))

        days_match = re.search(r"(?:within|in|delivery)\s*(\d+)\s*days?", text, re.IGNORECASE)
        if days_match: extracted["delivery_deadline_days"] = int(days_match.group(1))

        crore_match = re.search(r"[₹Rs\.]*\s*(\d+(?:\.\d+)?)\s*(?:crore|cr)", text, re.IGNORECASE)
        if crore_match:
            extracted["max_budget_paise"] = rupees_to_paise(float(crore_match.group(1)) * 10000000)
        else:
            lakh_match = re.search(r"[₹Rs\.]*\s*(\d+(?:\.\d+)?)\s*(?:lakh|lac|l)", text, re.IGNORECASE)
            if lakh_match:
                extracted["max_budget_paise"] = rupees_to_paise(float(lakh_match.group(1)) * 100000)
            else:
                num_budget = re.search(r"[₹Rs\.]*\s*(\d{5,12})", text, re.IGNORECASE)
                if num_budget:
                    extracted["max_budget_paise"] = rupees_to_paise(float(num_budget.group(1)))

        return extracted

    @classmethod
    def create_request(cls, title: str, department: str, item_name: str, quantity: int, specs: str, max_budget_paise: int, delivery_deadline_days: int, min_ram_gb: int = 16, min_storage_gb: int = 512, available_budget_paise: int = 0, raw_prompt: str = "", user_id: Optional[int] = None, actor_name: str = "Employee", db_path=None) -> Dict[str, Any]:
        if quantity <= 0 or max_budget_paise <= 0 or delivery_deadline_days <= 0:
            return {"success": False, "error": "Validation failed: Quantity, budget, and delivery timeline must be positive."}

        pr_id = f"PR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        conn = get_db_connection(db_path)
        conn.execute("""
            INSERT INTO purchase_requests (
                id, title, user_id, department, item_name, quantity, specs,
                min_ram_gb, min_storage_gb, max_budget_paise, delivery_deadline_days,
                available_budget_paise, additional_requirements, raw_prompt, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 'requirements_analyzed')
        """, (pr_id, title.strip(), user_id, department.strip(), item_name.strip(), quantity, specs.strip(), min_ram_gb, min_storage_gb, max_budget_paise, delivery_deadline_days, available_budget_paise, raw_prompt.strip()))
        conn.commit()
        conn.close()

        AuditService.log_event(
            action="CREATE_PURCHASE_REQUEST",
            stage="Requirement Analysis",
            actor=actor_name,
            request_id=pr_id,
            details={"quantity": quantity, "budget": format_inr(max_budget_paise), "deadline_days": delivery_deadline_days},
            db_path=db_path
        )
        return {"success": True, "pr_id": pr_id, "status": "requirements_analyzed"}

# Agent 2: Vendor Comparison
class VendorAgent:
    @classmethod
    def compare_vendors(cls, pr_id: str, actor_name: str = "System", auto_select: bool = True, db_path=None) -> Dict[str, Any]:
        conn = get_db_connection(db_path)
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if not pr:
            conn.close()
            return {"success": False, "error": "PR not found"}

        # Determine category and ensure matching vendors exist
        category = get_category_for_item(pr["item_name"])
        ensure_vendors_for_category(conn, category, pr["item_name"], pr["quantity"], pr["max_budget_paise"])

        # Only fetch vendors in the matching category
        vendors = conn.execute("SELECT * FROM vendors WHERE is_active = 1 AND category = ?", (category,)).fetchall()
        is_computing = category in ("Laptops & Hardware", "Monitors & Displays")
        comparison = []
        eligible = []

        for v in vendors:
            rejections = []
            # Only apply RAM/SSD checks for computing hardware
            if is_computing:
                if pr["min_ram_gb"] > 0 and v["ram_gb"] < pr["min_ram_gb"]:
                    rejections.append(f"RAM below requirement ({v['ram_gb']}GB < {pr['min_ram_gb']}GB required).")
                if pr["min_storage_gb"] > 0 and v["storage_gb"] < pr["min_storage_gb"]:
                    rejections.append(f"Storage below requirement ({v['storage_gb']}GB < {pr['min_storage_gb']}GB required).")
            if pr["delivery_deadline_days"] > 0 and v["delivery_days"] > pr["delivery_deadline_days"]:
                rejections.append(f"Delivery in {v['delivery_days']} days exceeds the {pr['delivery_deadline_days']}-day deadline.")

            total_cost_paise = pr["quantity"] * v["unit_price_paise"]
            if pr["max_budget_paise"] > 0 and total_cost_paise > pr["max_budget_paise"]:
                rejections.append(f"Total cost {format_inr(total_cost_paise)} exceeds budget of {format_inr(pr['max_budget_paise'])}.")

            is_elig = len(rejections) == 0
            entry = {
                "id": v["id"],
                "name": v["name"],
                "product_name": v["product_name"],
                "product_specs": v["product_specs"],
                "ram_gb": v["ram_gb"],
                "storage_gb": v["storage_gb"],
                "unit_price_paise": v["unit_price_paise"],
                "formatted_unit_price": format_inr(v["unit_price_paise"]),
                "total_cost_paise": total_cost_paise,
                "formatted_total_cost": format_inr(total_cost_paise),
                "utilization_pct": round((total_cost_paise / pr["max_budget_paise"] * 100), 1) if pr["max_budget_paise"] > 0 else 0,
                "delivery_days": v["delivery_days"],
                "warranty_years": v["warranty_years"],
                "rating": v["rating"],
                "is_eligible": is_elig,
                "rejection_reasons": rejections,
                "is_computing": is_computing
            }
            if is_elig:
                eligible.append(entry)
            comparison.append(entry)

        # L1 Principle: Lowest compliant cost with top quality & warranty
        eligible.sort(key=lambda x: (x["total_cost_paise"], -x["rating"], -x["warranty_years"]))
        recommended = eligible[0] if eligible else None
        explanation = ""

        if recommended:
            savings_paise = pr["max_budget_paise"] - recommended["total_cost_paise"]
            explanation = (
                f"{recommended['name']} offers the best overall commercial value for {pr['item_name']}, "
                f"providing the lowest compliant cost ({recommended['formatted_total_cost']}) among all eligible suppliers. "
                f"Delivers savings of {format_inr(savings_paise)} against approved budget, "
                f"with an enterprise {recommended['warranty_years']}-year corporate warranty, a {recommended['rating']}★ quality rating, and delivery in {recommended['delivery_days']} days."
            )
            if auto_select:
                conn.execute("UPDATE purchase_requests SET selected_vendor_id = ?, status = 'vendor_compared', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (recommended["id"], pr_id))
                conn.commit()
        else:
            explanation = f"No vendors in '{category}' met all constraints (delivery timeline + budget). Consider adjusting the budget or SLA deadline."

        conn.close()

        AuditService.log_event(
            action="COMPARE_VENDORS",
            stage="Vendor Comparison",
            actor=actor_name,
            request_id=pr_id,
            details={"evaluated": len(vendors), "recommended": recommended["name"] if recommended else None},
            db_path=db_path
        )

        return {
            "success": True,
            "pr_id": pr_id,
            "total_evaluated": len(vendors),
            "eligible_count": len(eligible),
            "vendors": comparison,
            "recommended_vendor": recommended,
            "explanation": explanation
        }

# Agent 3: Budget and Policy
class BudgetPolicyAgent:
    @classmethod
    def validate_budget_and_policy(cls, pr_id: str, actor_name: str = "System", db_path=None) -> Dict[str, Any]:
        conn = get_db_connection(db_path)
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if not pr:
            conn.close()
            return {"success": False, "error": "PR not found"}

        vendor = None
        if pr["selected_vendor_id"]:
            vendor = conn.execute("SELECT * FROM vendors WHERE id = ?", (pr["selected_vendor_id"],)).fetchone()

        dept_budget = conn.execute("SELECT * FROM department_budgets WHERE department = ?", (pr["department"],)).fetchone()
        policies = conn.execute("SELECT * FROM company_policies").fetchall()

        unit_price = vendor["unit_price_paise"] if vendor else 0
        total_cost_paise = pr["quantity"] * unit_price

        # Prioritize available department budget entered by user in Agent 1
        if pr["available_budget_paise"] and pr["available_budget_paise"] > 0:
            total_budget_paise = pr["available_budget_paise"]
        elif dept_budget:
            total_budget_paise = dept_budget["total_budget_paise"]
        else:
            total_budget_paise = 5000000000

        allocated_paise = dept_budget["allocated_paise"] if dept_budget else 0
        available_budget_paise = total_budget_paise - allocated_paise
        remaining_budget_paise = available_budget_paise - total_cost_paise

        budget_ok = remaining_budget_paise >= 0
        exceptions = []

        if not budget_ok:
            exceptions.append(f"Insufficient departmental budget. Required {format_inr(total_cost_paise)}, available {format_inr(available_budget_paise)}.")

        # Determine Approval Level
        approval_req = "Manager Approval"
        if total_cost_paise >= 500000000: # >= ₹50 Lakhs
            approval_req = "Executive Board / VP Approval Required (> ₹50 Lakhs)"
        elif total_cost_paise >= 5000000: # >= ₹50,000
            approval_req = "Department Manager Approval Required (≥ ₹50,000)"
        else:
            approval_req = "Standard Low-Value Purchase"

        # Check existing approval - never downgrade an already-decided status
        approval_row = conn.execute("SELECT * FROM purchase_approvals WHERE pr_id = ? ORDER BY decided_at DESC", (pr_id,)).fetchone()
        has_approval = approval_row and approval_row["decision"] == "approved"

        # Only move to pending if not yet decided and budget is OK
        current_pr_status = pr["status"]
        already_decided = current_pr_status in ("purchase_approved", "purchase_rejected", "po_generated", "invoice_matched", "payment_completed")
        if not has_approval and budget_ok and not already_decided:
            conn.execute("UPDATE purchase_requests SET status = 'pending_purchase_approval', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (pr_id,))
            conn.commit()

        conn.close()

        AuditService.log_event(
            action="VALIDATE_BUDGET_POLICY",
            stage="Budget and Policy",
            actor=actor_name,
            request_id=pr_id,
            details={"total_cost": format_inr(total_cost_paise), "budget_ok": budget_ok, "approval_req": approval_req},
            db_path=db_path
        )

        return {
            "success": True,
            "pr_id": pr_id,
            "total_cost_paise": total_cost_paise,
            "formatted_total_cost": format_inr(total_cost_paise),
            "available_budget_paise": available_budget_paise,
            "formatted_available_budget": format_inr(available_budget_paise),
            "remaining_budget_paise": remaining_budget_paise,
            "formatted_remaining_budget": format_inr(remaining_budget_paise),
            "formatted_total_budget": format_inr(total_budget_paise),
            "utilization_pct": round((total_cost_paise / total_budget_paise) * 100, 1) if total_budget_paise else 0,
            "budget_ok": budget_ok,
            "policy_ok": len(exceptions) == 0,
            "approval_requirement": approval_req,
            "exceptions": exceptions
        }

# Agent 4: Purchase Order
class PurchaseOrderAgent:
    @classmethod
    def generate_po(cls, pr_id: str, delivery_terms: str = "FOB Destination", payment_terms: str = "Net 30 Days upon 3-Way Invoice Match", created_by: Optional[int] = None, actor_name: str = "System", db_path=None) -> Dict[str, Any]:
        conn = get_db_connection(db_path)
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if not pr:
            conn.close()
            return {"success": False, "error": "PR not found"}

        # Prevent duplicate PO
        existing_po = conn.execute("SELECT * FROM purchase_orders WHERE pr_id = ?", (pr_id,)).fetchone()
        if existing_po:
            conn.close()
            return {"success": True, "po_id": existing_po["id"], "po": dict(existing_po), "duplicate": True}

        # Check Approval
        if pr["status"] != "purchase_approved":
            conn.close()
            return {"success": False, "error": f"Cannot generate PO. PR status is '{pr['status']}', requires 'purchase_approved'."}

        vendor = conn.execute("SELECT * FROM vendors WHERE id = ?", (pr["selected_vendor_id"],)).fetchone()
        if not vendor:
            conn.close()
            return {"success": False, "error": "Selected vendor not found."}

        po_id = f"PO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        unit_price = vendor["unit_price_paise"]
        total_amount = pr["quantity"] * unit_price

        conn.execute("""
            INSERT INTO purchase_orders (id, pr_id, vendor_id, item_name, quantity, unit_price_paise, total_amount_paise, delivery_terms, payment_terms, status, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'issued', ?)
        """, (po_id, pr_id, vendor["id"], pr["item_name"], pr["quantity"], unit_price, total_amount, delivery_terms, payment_terms, created_by))

        # Update PR status
        conn.execute("UPDATE purchase_requests SET status = 'po_generated', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (pr_id,))
        conn.commit()
        conn.close()

        # ERP Sync: Create initial goods receipt in warehouse system
        ERPService.create_goods_receipt(po_id, pr["item_name"], pr["quantity"], pr["quantity"], "accepted", "Automated warehouse scanner verification passed.", db_path=db_path)

        AuditService.log_event(
            action="GENERATE_PURCHASE_ORDER",
            stage="Purchase Order",
            actor=actor_name,
            request_id=pr_id,
            details={"po_id": po_id, "vendor": vendor["name"], "total": format_inr(total_amount)},
            db_path=db_path
        )

        return {"success": True, "po_id": po_id, "status": "po_generated"}

# Agent 5: Invoice Matching
class InvoiceAgent:
    @classmethod
    def match_invoice(cls, po_id: str, pr_id: str, invoice_number: str, vendor_name: str, item_name: str, quantity: int, unit_price_paise: int, total_amount_paise: int, file_path: str = "", original_filename: str = "", actor_name: str = "System", db_path=None) -> Dict[str, Any]:
        conn = get_db_connection(db_path)
        po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        if not po:
            conn.close()
            return {"success": False, "error": "PO not found"}

        grn = conn.execute("SELECT * FROM goods_receipts WHERE po_id = ? ORDER BY received_at DESC", (po_id,)).fetchone()

        # Strict 3-Way Matching Checks
        po_matched = (po["id"] == po_id)
        quantity_matched = (quantity == po["quantity"])
        price_matched = (unit_price_paise == po["unit_price_paise"])
        total_matched = (total_amount_paise == po["total_amount_paise"])

        grn_matched = False
        if grn:
            grn_matched = (grn["quantity_received"] >= quantity) and (grn["inspection_status"] == "accepted")

        discrepancies = []
        if not quantity_matched:
            discrepancies.append(f"Quantity mismatch: Invoiced {quantity} units vs PO {po['quantity']} units.")
        if not price_matched:
            discrepancies.append(f"Price mismatch: Invoiced unit rate {format_inr(unit_price_paise)} vs PO {format_inr(po['unit_price_paise'])}.")
        if not total_matched:
            discrepancies.append(f"Total mismatch: Invoiced total {format_inr(total_amount_paise)} vs PO {format_inr(po['total_amount_paise'])}.")
        if not grn_matched:
            discrepancies.append("Warehouse Goods Receipt mismatch: Quantity not fully received or inspection not accepted.")

        is_perfect_match = len(discrepancies) == 0
        match_status = "matched" if is_perfect_match else "mismatch"
        inv_id = f"INV-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

        match_details = {
            "po_matched": po_matched,
            "quantity_matched": quantity_matched,
            "price_matched": price_matched,
            "total_matched": total_matched,
            "grn_matched": grn_matched,
            "extracted_po": po_id,
            "formatted_invoice_unit_price": format_inr(unit_price_paise),
            "discrepancies": discrepancies,
            "explanation": "All 3-way criteria verified (PO, Invoice OCR, and Goods Receipt Note match with 0% variance)." if is_perfect_match else "Discrepancy detected: " + "; ".join(discrepancies)
        }

        conn.execute("""
            INSERT INTO invoices (id, po_id, vendor_name, invoice_number, invoice_date, item_name, quantity, unit_price_paise, total_amount_paise, file_path, original_filename, match_status, match_details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (inv_id, po_id, vendor_name, invoice_number, datetime.now().strftime("%Y-%m-%d"), item_name, quantity, unit_price_paise, total_amount_paise, file_path, original_filename, match_status, json.dumps(match_details)))

        new_pr_status = "invoice_matched" if is_perfect_match else "invoice_mismatched"
        conn.execute("UPDATE purchase_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_pr_status, pr_id))
        conn.commit()
        conn.close()

        AuditService.log_event(
            action="MATCH_INVOICE",
            stage="Invoice Matching",
            actor=actor_name,
            request_id=pr_id,
            details={"invoice_id": inv_id, "match_status": match_status, "discrepancies": discrepancies},
            db_path=db_path
        )

        return {
            "success": True,
            "invoice_id": inv_id,
            "match_status": match_status,
            "match_details": match_details
        }

# Agent 6: Approval and Payment
class ApprovalPaymentAgent:
    @classmethod
    def execute_payment(cls, invoice_id: str, po_id: str, pr_id: str, payment_method: str = "Online", simulate_failure: bool = False, actor_name: str = "System", db_path=None) -> Dict[str, Any]:
        conn = get_db_connection(db_path)
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv:
            conn.close()
            return {"success": False, "error": "Invoice not found"}

        if inv["match_status"] != "matched":
            conn.close()
            return {"success": False, "error": f"Cannot execute payment. Invoice match status is '{inv['match_status']}', requires 'matched'."}

        amount_paise = inv["total_amount_paise"]
        idempotency_key = f"IDEM-{invoice_id}-{amount_paise}"
        conn.close()

        res = PaymentService.process_payment(
            invoice_id=invoice_id,
            po_id=po_id,
            pr_id=pr_id,
            amount_paise=amount_paise,
            idempotency_key=idempotency_key,
            payment_method=payment_method,
            simulate_failure=simulate_failure,
            db_path=db_path
        )

        AuditService.log_event(
            action="EXECUTE_PAYMENT",
            stage="Approval and Payment",
            actor=actor_name,
            request_id=pr_id,
            details=res,
            db_path=db_path
        )
        return res

# ==============================================================================
# LANGGRAPH MULTI-AGENT ORCHESTRATION WORKFLOW
# ==============================================================================
class ProcurementWorkflowState(TypedDict):
    request_id: str
    stage: str
    requirements: Dict[str, Any]
    vendor_comparison: Dict[str, Any]
    selected_vendor_id: Optional[int]
    budget_result: Dict[str, Any]
    purchase_approval: Optional[Dict[str, Any]]
    purchase_order: Optional[Dict[str, Any]]
    invoice: Optional[Dict[str, Any]]
    invoice_match: Optional[Dict[str, Any]]
    payment_result: Optional[Dict[str, Any]]
    exceptions: List[str]
    is_paused: bool
    pause_reason: Optional[str]

def node_requirements(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    pr_id = state.get("request_id")
    AuditService.log_event("WORKFLOW_NODE_REQUIREMENTS", "LangGraph", "WorkflowEngine", request_id=pr_id)
    state["stage"] = "requirements_analyzed"
    return state

def node_vendors(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    pr_id = state.get("request_id")
    res = VendorAgent.compare_vendors(pr_id, actor_name="LangGraphEngine")
    state["vendor_comparison"] = res
    if res.get("recommended_vendor"):
        state["selected_vendor_id"] = res["recommended_vendor"]["id"]
        state["stage"] = "vendor_compared"
    else:
        state["exceptions"].append("No eligible vendors found.")
        state["stage"] = "blocked"
    return state

def node_budget(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    pr_id = state.get("request_id")
    res = BudgetPolicyAgent.validate_budget_and_policy(pr_id, actor_name="LangGraphEngine")
    state["budget_result"] = res
    if not res.get("budget_ok"):
        state["exceptions"].extend(res.get("exceptions", []))
        state["stage"] = "budget_failed"
    else:
        state["stage"] = "pending_purchase_approval"
        state["is_paused"] = True
        state["pause_reason"] = "Awaiting manager authorization."
    return state

def node_purchase_order(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    pr_id = state.get("request_id")
    res = PurchaseOrderAgent.generate_po(pr_id, actor_name="LangGraphEngine")
    if res.get("success"):
        state["purchase_order"] = res
        state["stage"] = "po_generated"
        state["is_paused"] = True
        state["pause_reason"] = "Awaiting supplier invoice upload."
    return state

def node_invoice_matching(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    state["stage"] = "invoice_matching"
    return state

def node_payment(state: ProcurementWorkflowState) -> ProcurementWorkflowState:
    pr_id = state.get("request_id")
    conn = get_db_connection()
    inv = conn.execute("SELECT * FROM invoices WHERE po_id = (SELECT id FROM purchase_orders WHERE pr_id = ?) ORDER BY created_at DESC", (pr_id,)).fetchone()
    conn.close()
    if inv and inv["match_status"] == "matched":
        res = ApprovalPaymentAgent.execute_payment(inv["id"], inv["po_id"], pr_id, actor_name="LangGraphEngine")
        state["payment_result"] = res
        state["stage"] = "payment_completed"
    return state

def build_procurement_graph():
    builder = StateGraph(ProcurementWorkflowState)
    builder.add_node("requirement_analysis", node_requirements)
    builder.add_node("vendor_comparison", node_vendors)
    builder.add_node("budget_policy", node_budget)
    builder.add_node("purchase_order", node_purchase_order)
    builder.add_node("invoice_matching", node_invoice_matching)
    builder.add_node("approval_payment", node_payment)

    builder.set_entry_point("requirement_analysis")
    builder.add_edge("requirement_analysis", "vendor_comparison")
    builder.add_edge("vendor_comparison", "budget_policy")

    def route_after_budget(state: ProcurementWorkflowState):
        if state.get("is_paused") or state.get("stage") == "pending_purchase_approval":
            return END
        return "purchase_order"

    builder.add_conditional_edges("budget_policy", route_after_budget, {END: END, "purchase_order": "purchase_order"})
    builder.add_edge("purchase_order", "invoice_matching")
    builder.add_edge("invoice_matching", "approval_payment")
    builder.add_edge("approval_payment", END)

    return builder.compile()

procurement_graph = build_procurement_graph()

# ==============================================================================
# FLASK WEB APPLICATION & CSRF
# ==============================================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "procure-tower-secure-secret-2026")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

def generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    return session["_csrf_token"]

@app.before_request
def check_csrf():
    if app.config.get("TESTING") and not app.config.get("WTF_CSRF_ENABLED", False):
        return
    if request.method == "POST":
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not token or token != session.get("_csrf_token"):
            if not request.path.startswith("/api/"):
                abort(403, description="CSRF token validation failed.")

@app.context_processor
def inject_csrf_and_stats():
    conn = get_db_connection()
    pending_app_count = conn.execute("SELECT COUNT(*) FROM purchase_requests WHERE status = 'pending_purchase_approval'").fetchone()[0]
    conn.close()
    return {
        "csrf_token": generate_csrf_token(),
        "format_inr": format_inr,
        "format_inr_short": format_inr_short,
        "pending_approvals_count": pending_app_count
    }

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for("signin", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def role_required(allowed_roles):
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("signin"))
            if session.get("user_role") not in allowed_roles:
                flash(f"Access denied. Requires one of roles: {', '.join(allowed_roles)}.", "error")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==============================================================================
# FLASK ROUTES: AUTH & DASHBOARD
# ==============================================================================
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        department = request.form.get("department", "IT")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if len(full_name) < 2: errors.append("Full name must be at least 2 characters.")
        if "@" not in email or "." not in email: errors.append("Valid work email is required.")
        if len(password) < 8: errors.append("Password must be at least 8 characters.")
        if password != confirm_password: errors.append("Passwords do not match.")

        conn = get_db_connection()
        existing = conn.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()
        if existing:
            errors.append("An account with this email already exists.")

        if errors:
            conn.close()
            for err in errors: flash(err, "error")
            return render_template("signup.html", full_name=full_name, email=email, department=department)

        # Enforce public signup default role 'employee'
        pwd_hash = generate_password_hash(password)
        conn.execute("""
            INSERT INTO users (full_name, email, password_hash, department, role)
            VALUES (?, ?, ?, ?, 'employee')
        """, (full_name, email, pwd_hash, department))
        conn.commit()
        conn.close()

        AuditService.log_event("USER_REGISTRATION", "Auth", full_name, details={"email": email, "role": "employee"})
        flash("Registration successful! Please sign in with your credentials.", "success")
        return redirect(url_for("signin"))

    return render_template("signup.html")

@app.route("/signin", methods=["GET", "POST"])
def signin():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["full_name"]
            session["user_email"] = user["email"]
            session["user_role"] = user["role"]
            session["department"] = user["department"]

            AuditService.log_event("USER_LOGIN", "Auth", user["full_name"], details={"email": email, "role": user["role"]})
            flash(f"Welcome back, {user['full_name']}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid email or password. Please verify and try again.", "error")
            return render_template("signin.html", email=email)

    return render_template("signin.html")

@app.route("/logout")
def logout():
    name = session.get("user_name", "User")
    session.clear()
    flash(f"You have signed out successfully.", "info")
    return redirect(url_for("signin"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db_connection()
    user_role = session.get("user_role", "employee")
    user_id = session.get("user_id")

    total_pr = conn.execute("SELECT COUNT(*) FROM purchase_requests").fetchone()[0]
    pending_app = conn.execute("SELECT COUNT(*) FROM purchase_requests WHERE status = 'pending_purchase_approval'").fetchone()[0]
    total_po = conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
    invoices_review = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    invoice_exceptions = conn.execute("SELECT COUNT(*) FROM invoices WHERE match_status != 'matched'").fetchone()[0]
    pending_pay = conn.execute("SELECT COUNT(*) FROM invoices WHERE match_status = 'matched' AND id NOT IN (SELECT invoice_id FROM payments WHERE status = 'completed')").fetchone()[0]
    payments_completed = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'completed'").fetchone()[0]

    # ── Role-specific: Employee "My Requests" list ──────────────────────────────
    my_prs = []
    if user_role == "employee":
        my_pr_rows = conn.execute("""
            SELECT pr.*,
                   pa.decision    AS approval_decision,
                   pa.comment     AS approval_comment,
                   pa.decided_at  AS approval_decided_at,
                   u.full_name    AS approver_name
            FROM purchase_requests pr
            LEFT JOIN purchase_approvals pa ON pa.pr_id = pr.id
                AND pa.id = (SELECT MAX(id) FROM purchase_approvals WHERE pr_id = pr.id)
            LEFT JOIN users u ON u.id = pa.approver_id
            WHERE pr.user_id = ?
            ORDER BY pr.created_at DESC
        """, (user_id,)).fetchall()
        for r in my_pr_rows:
            d = dict(r)
            d["formatted_budget"] = format_inr(d["max_budget_paise"])
            my_prs.append(d)

    # ── Universal pending approval queue (available to all users) ─────────────
    pend_rows = conn.execute("""
        SELECT pr.*, v.name AS vendor_name, v.delivery_days,
               (pr.quantity * v.unit_price_paise) AS total_cost_calc,
               u.full_name AS requester_name
        FROM purchase_requests pr
        LEFT JOIN vendors v ON pr.selected_vendor_id = v.id
        LEFT JOIN users u ON pr.user_id = u.id
        WHERE pr.status = 'pending_purchase_approval'
        ORDER BY pr.created_at DESC
    """).fetchall()
    pending_prs_mgr = []
    for r in pend_rows:
        d = dict(r)
        d["formatted_budget"] = format_inr(d["max_budget_paise"])
        d["formatted_total"] = format_inr(d["total_cost_calc"] or d["max_budget_paise"])
        pending_prs_mgr.append(d)

    # Department Budgets Data for Chart 1
    dept_rows = conn.execute("SELECT * FROM department_budgets").fetchall()
    chart_dept_labels = []
    chart_dept_total = []
    chart_dept_committed = []
    for d in dept_rows:
        dept_name = d["department"]
        total_cr = round(d["total_budget_paise"] / 1000000000.0, 2)
        comm_row = conn.execute("""
            SELECT SUM(po.total_amount_paise) FROM purchase_orders po
            JOIN purchase_requests pr ON po.pr_id = pr.id
            WHERE pr.department = ?
        """, (dept_name,)).fetchone()
        comm_paise = comm_row[0] or 0
        comm_cr = round(comm_paise / 1000000000.0, 2)
        chart_dept_labels.append(dept_name)
        chart_dept_total.append(total_cr)
        chart_dept_committed.append(comm_cr)

    # Workflow Status Distribution for Chart 2
    status_counts = {
        "Requirements": conn.execute("SELECT COUNT(*) FROM purchase_requests WHERE status = 'requirements_analyzed'").fetchone()[0],
        "Vendor Compared": conn.execute("SELECT COUNT(*) FROM purchase_requests WHERE status = 'vendor_compared'").fetchone()[0],
        "Pending Approval": pending_app,
        "PO Generated": conn.execute("SELECT COUNT(*) FROM purchase_requests WHERE status IN ('purchase_approved', 'po_generated')").fetchone()[0],
        "Invoice Matched": conn.execute("SELECT COUNT(*) FROM purchase_requests WHERE status = 'invoice_matched'").fetchone()[0],
        "Payment Completed": payments_completed
    }
    stage_labels = list(status_counts.keys())
    stage_data = [max(c, 0) for c in status_counts.values()]

    # Vendor Benchmark Data for Chart 3
    vendors = conn.execute("SELECT name, rating, delivery_days, (unit_price_paise / 100000.0) as unit_price_thousands FROM vendors WHERE is_active = 1").fetchall()
    vendor_names = [v["name"] for v in vendors]
    vendor_ratings = [v["rating"] for v in vendors]
    vendor_delivery = [v["delivery_days"] for v in vendors]
    vendor_prices = [round(v["unit_price_thousands"], 1) for v in vendors]

    conn.close()

    stats = {
        "total_pr": total_pr,
        "pending_purchase_approvals": pending_app,
        "total_po": total_po,
        "invoices_review": invoices_review,
        "invoice_exceptions": invoice_exceptions,
        "pending_payment_approvals": pending_pay,
        "payments_completed": payments_completed
    }

    chart_data = {
        "dept_labels": chart_dept_labels,
        "dept_total": chart_dept_total,
        "dept_committed": chart_dept_committed,
        "stage_labels": stage_labels,
        "stage_data": stage_data,
        "vendor_names": vendor_names,
        "vendor_ratings": vendor_ratings,
        "vendor_delivery": vendor_delivery,
        "vendor_prices": vendor_prices
    }

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        stats=stats,
        chart_data=chart_data,
        my_prs=my_prs,
        pending_prs_mgr=pending_prs_mgr
    )

# ==============================================================================
# FLASK ROUTES: SIX SPECIALIZED AGENTS
# ==============================================================================

# Agent 1: Requirements
@app.route("/agents/requirements", methods=["GET", "POST"])
@login_required
def agent_requirements():
    conn = get_db_connection()
    latest_result = None

    if request.method == "POST":
        natural_prompt = request.form.get("natural_prompt", "").strip()
        title = request.form.get("title", "Procurement Requisition").strip()
        department = request.form.get("department", session.get("department", "IT"))
        item_name = request.form.get("item_name", "").strip()
        quantity = int(request.form.get("quantity", 0))
        max_budget_rupees = float(request.form.get("max_budget_rupees", 0))
        delivery_deadline_days = int(request.form.get("delivery_deadline_days", 7))
        min_ram_gb = int(request.form.get("min_ram_gb", 0))
        min_storage_gb = int(request.form.get("min_storage_gb", 0))
        specs = request.form.get("specs", "")

        # Always parse natural language prompt when provided — it always takes priority
        if natural_prompt:
            parsed = RequirementAgent.parse_natural_language(natural_prompt)
            # Override form fields with parsed values
            if parsed.get("item_name") and not item_name:
                item_name = parsed["item_name"]
            if parsed.get("quantity") and parsed["quantity"] > 0 and quantity == 0:
                quantity = parsed["quantity"]
            if not min_ram_gb:
                min_ram_gb = parsed.get("min_ram_gb", 0)
            if not min_storage_gb:
                min_storage_gb = parsed.get("min_storage_gb", 0)
            if delivery_deadline_days == 7:
                delivery_deadline_days = parsed.get("delivery_deadline_days", 7)
            if max_budget_rupees == 0 and parsed.get("max_budget_paise", 0) > 0:
                max_budget_rupees = paise_to_rupees(parsed["max_budget_paise"])

        # Ensure non-computing items default RAM/SSD to 0
        category = get_category_for_item(item_name)
        if category not in ("Laptops & Hardware", "Monitors & Displays"):
            min_ram_gb = 0
            min_storage_gb = 0

        # Fallback defaults
        if not item_name:
            item_name = "General Procurement Item"
        if quantity <= 0:
            quantity = 1
        if max_budget_rupees <= 0:
            max_budget_rupees = 500000

        # Available department budget specified by user
        available_dept_budget_rupees = float(request.form.get("available_dept_budget", 0) or 0)
        if available_dept_budget_rupees <= 0:
            available_dept_budget_rupees = max(max_budget_rupees * 2, 1500000)
        available_budget_paise = rupees_to_paise(available_dept_budget_rupees)

        # Update department budgets table baseline for this department
        conn.execute("""
            INSERT INTO department_budgets (department, total_budget_paise, allocated_paise, fiscal_year)
            VALUES (?, ?, 0, 'FY26')
            ON CONFLICT(department) DO UPDATE SET total_budget_paise = excluded.total_budget_paise, allocated_paise = 0
        """, (department, available_budget_paise))
        conn.commit()

        max_budget_paise = rupees_to_paise(max_budget_rupees)
        res = RequirementAgent.create_request(
            title=title,
            department=department,
            item_name=item_name,
            quantity=quantity,
            specs=specs,
            max_budget_paise=max_budget_paise,
            delivery_deadline_days=delivery_deadline_days,
            min_ram_gb=min_ram_gb,
            min_storage_gb=min_storage_gb,
            available_budget_paise=available_budget_paise,
            raw_prompt=natural_prompt,
            user_id=session.get("user_id"),
            actor_name=session.get("user_name", "Employee")
        )

        if res.get("success"):
            flash(f"Purchase Request {res['pr_id']} successfully analyzed and stored.", "success")
            return redirect(url_for("agent_vendors", pr_id=res["pr_id"]))
        else:
            flash(res.get("error", "Error creating purchase request."), "error")

    all_prs_rows = conn.execute("SELECT * FROM purchase_requests ORDER BY created_at DESC").fetchall()
    all_prs = []
    for r in all_prs_rows:
        d = dict(r)
        d["formatted_budget"] = format_inr(d["max_budget_paise"])
        all_prs.append(d)
    conn.close()

    return render_template("agent_requirements.html", active_page="agent_requirements", all_prs=all_prs, latest_result=latest_result)

# Agent 2: Vendors
@app.route("/agents/vendors", methods=["GET", "POST"])
@login_required
def agent_vendors():
    conn = get_db_connection()
    pr_id = request.form.get("pr_id") or request.args.get("pr_id")
    if not pr_id:
        latest = conn.execute("SELECT id FROM purchase_requests ORDER BY created_at DESC LIMIT 1").fetchone()
        pr_id = latest["id"] if latest else None

    current_pr = None
    comparison = None
    all_prs = []

    if pr_id:
        pr_row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if pr_row:
            current_pr = dict(pr_row)
            current_pr["formatted_budget"] = format_inr(current_pr["max_budget_paise"])
            comparison = VendorAgent.compare_vendors(pr_id, actor_name=session.get("user_name", "User"))

    prs_rows = conn.execute("SELECT id, item_name FROM purchase_requests ORDER BY created_at DESC").fetchall()
    all_prs = [dict(r) for r in prs_rows]
    conn.close()

    return render_template("agent_vendors.html", active_page="agent_vendors", current_pr=current_pr, comparison=comparison, all_prs=all_prs)

# Agent 3: Budget & Policy
@app.route("/agents/budget-policy", methods=["GET", "POST"])
@login_required
def agent_budget_policy():
    conn = get_db_connection()
    pr_id = request.form.get("pr_id") or request.args.get("pr_id")
    if not pr_id:
        latest = conn.execute("SELECT id FROM purchase_requests ORDER BY created_at DESC LIMIT 1").fetchone()
        pr_id = latest["id"] if latest else None

    current_pr = None
    selected_vendor = None
    validation = None
    dept_budget = None
    approval_record = None

    if pr_id:
        # Validate budget and policy first (transitions status to pending_purchase_approval if compliant)
        validation = BudgetPolicyAgent.validate_budget_and_policy(pr_id, actor_name=session.get("user_name", "User"))

        pr_row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if pr_row:
            current_pr = dict(pr_row)
            current_pr["formatted_budget"] = format_inr(current_pr["max_budget_paise"])

            if current_pr.get("selected_vendor_id"):
                v_row = conn.execute("SELECT * FROM vendors WHERE id = ?", (current_pr["selected_vendor_id"],)).fetchone()
                if v_row:
                    selected_vendor = dict(v_row)
                    selected_vendor["formatted_unit_price"] = format_inr(selected_vendor["unit_price_paise"])

            b_row = conn.execute("SELECT * FROM department_budgets WHERE department = ?", (current_pr["department"],)).fetchone()
            dept_budget = dict(b_row) if b_row else None

            app_row = conn.execute("""
                SELECT a.*, u.full_name as approver_name FROM purchase_approvals a
                JOIN users u ON a.approver_id = u.id WHERE a.pr_id = ? ORDER BY a.decided_at DESC LIMIT 1
            """, (pr_id,)).fetchone()
            approval_record = dict(app_row) if app_row else None

    conn.close()
    return render_template("agent_budget_policy.html", active_page="agent_budget_policy", current_pr=current_pr, selected_vendor=selected_vendor, validation=validation, dept_budget=dept_budget, approval_record=approval_record)

# Agent 4: Purchase Order
@app.route("/agents/purchase-orders", methods=["GET", "POST"])
@login_required
def agent_purchase_order():
    conn = get_db_connection()
    pr_id = request.form.get("pr_id") or request.args.get("pr_id")
    if not pr_id:
        latest = conn.execute("SELECT id FROM purchase_requests ORDER BY created_at DESC LIMIT 1").fetchone()
        pr_id = latest["id"] if latest else None

    if request.method == "POST":
        delivery_terms = request.form.get("delivery_terms", "FOB Destination")
        payment_terms = request.form.get("payment_terms", "Net 30 Days")
        res = PurchaseOrderAgent.generate_po(
            pr_id=pr_id,
            delivery_terms=delivery_terms,
            payment_terms=payment_terms,
            created_by=session.get("user_id"),
            actor_name=session.get("user_name", "User")
        )
        if res.get("success"):
            flash(f"Purchase Order {res['po_id']} issued successfully!", "success")
        else:
            flash(res.get("error", "Error issuing PO."), "error")
        return redirect(url_for("agent_purchase_order", pr_id=pr_id))

    current_pr = None
    existing_po = None
    vendor = None

    if pr_id:
        pr_row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if pr_row:
            current_pr = dict(pr_row)
            current_pr["formatted_budget"] = format_inr(current_pr["max_budget_paise"])

            po_row = conn.execute("SELECT * FROM purchase_orders WHERE pr_id = ?", (pr_id,)).fetchone()
            if po_row:
                existing_po = dict(po_row)
                existing_po["formatted_unit_price"] = format_inr(existing_po["unit_price_paise"])
                existing_po["formatted_total"] = format_inr(existing_po["total_amount_paise"])

            if current_pr.get("selected_vendor_id"):
                v_row = conn.execute("SELECT * FROM vendors WHERE id = ?", (current_pr["selected_vendor_id"],)).fetchone()
                vendor = dict(v_row) if v_row else None

            # Fetch latest approval/rejection record for display
            rejection_row = conn.execute("""
                SELECT a.decision, a.comment, a.decided_at, u.full_name as approver_name
                FROM purchase_approvals a
                JOIN users u ON a.approver_id = u.id
                WHERE a.pr_id = ?
                ORDER BY a.decided_at DESC LIMIT 1
            """, (pr_id,)).fetchone()
            if rejection_row:
                current_pr["approval_decision"] = rejection_row["decision"]
                current_pr["approval_comment"] = rejection_row["comment"]
                current_pr["approver_name"] = rejection_row["approver_name"]
                current_pr["approval_decided_at"] = rejection_row["decided_at"]

    conn.close()
    return render_template("agent_purchase_order.html", active_page="agent_purchase_order", current_pr=current_pr, existing_po=existing_po, vendor=vendor)

# Agent 5: Invoice Matching
@app.route("/agents/invoice-matching", methods=["GET", "POST"])
@login_required
def agent_invoice_matching():
    conn = get_db_connection()
    pr_id = request.form.get("pr_id") or request.args.get("pr_id")
    po_id = request.form.get("po_id") or request.args.get("po_id")

    if not po_id and pr_id:
        po_row = conn.execute("SELECT id FROM purchase_orders WHERE pr_id = ?", (pr_id,)).fetchone()
        if po_row: po_id = po_row["id"]
    elif not po_id:
        po_row = conn.execute("SELECT id, pr_id FROM purchase_orders ORDER BY created_at DESC LIMIT 1").fetchone()
        if po_row:
            po_id = po_row["id"]
            pr_id = po_row["pr_id"]

    po = None
    vendor = None
    current_pr = None
    grn = None

    if po_id:
        p_row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        if p_row:
            po = dict(p_row)
            po["formatted_unit_price"] = format_inr(po["unit_price_paise"])
            po["formatted_total"] = format_inr(po["total_amount_paise"])
            pr_id = po["pr_id"]

            pr_row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
            current_pr = dict(pr_row) if pr_row else None

            v_row = conn.execute("SELECT * FROM vendors WHERE id = ?", (po["vendor_id"],)).fetchone()
            vendor = dict(v_row) if v_row else None

            g_row = conn.execute("SELECT * FROM goods_receipts WHERE po_id = ? ORDER BY received_at DESC", (po_id,)).fetchone()
            grn = dict(g_row) if g_row else None

    if request.method == "POST":
        sample_type = request.form.get("sample_type")
        file = request.files.get("invoice_file")

        if not po:
            flash("No active PO found to match invoice against.", "error")
            conn.close()
            return redirect(url_for("agent_invoice_matching"))

        inv_num = f"INV-2026-{secrets.token_hex(3).upper()}"
        file_path = ""
        orig_filename = ""
        inv_vendor = vendor["name"] if vendor else "TechNova Solutions"
        inv_item = po["item_name"]

        # Default values from PO
        inv_qty = po["quantity"]
        inv_unit_price = po["unit_price_paise"]
        inv_total = po["total_amount_paise"]

        if sample_type == "perfect_match":
            # Exact match
            inv_num = f"INV-MATCH-{secrets.token_hex(2).upper()}"
            pdf_path = generate_sample_invoice_pdf("Sample_Perfect_Match.pdf", po["id"], inv_num, inv_vendor, inv_item, inv_qty, paise_to_rupees(inv_unit_price))
            file_path = pdf_path
            orig_filename = "Sample_Perfect_Match.pdf"
        elif sample_type == "price_mismatch":
            # Price overbilling variance: e.g. ₹52,000 instead of ₹48,000
            inv_num = f"INV-MIS-PRICE-{secrets.token_hex(2).upper()}"
            inv_unit_price = inv_unit_price + 400000 # +₹4,000 per unit variance
            inv_total = inv_qty * inv_unit_price
            pdf_path = generate_sample_invoice_pdf("Sample_Price_Mismatch.pdf", po["id"], inv_num, inv_vendor, inv_item, inv_qty, paise_to_rupees(inv_unit_price))
            file_path = pdf_path
            orig_filename = "Sample_Price_Mismatch.pdf"
        elif sample_type == "quantity_mismatch":
            # Quantity discrepancy: 550 units instead of 500
            inv_num = f"INV-MIS-QTY-{secrets.token_hex(2).upper()}"
            inv_qty = po["quantity"] + 50
            inv_total = inv_qty * inv_unit_price
            pdf_path = generate_sample_invoice_pdf("Sample_Qty_Mismatch.pdf", po["id"], inv_num, inv_vendor, inv_item, inv_qty, paise_to_rupees(inv_unit_price))
            file_path = pdf_path
            orig_filename = "Sample_Qty_Mismatch.pdf"
        elif file and file.filename:
            orig_filename = secure_filename(file.filename)
            saved_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{orig_filename}"
            file_path = os.path.join(UPLOAD_FOLDER, saved_name)
            file.save(file_path)

            extracted_text = OCRService.extract_text(file_path)
            parsed = OCRService.parse_invoice_text(extracted_text)

            if parsed.get("invoice_number"): inv_num = parsed["invoice_number"]
            if parsed.get("vendor_name"): inv_vendor = parsed["vendor_name"]
            if parsed.get("quantity"): inv_qty = parsed["quantity"]
            if parsed.get("unit_price_paise"): inv_unit_price = parsed["unit_price_paise"]
            if parsed.get("total_amount_paise"): inv_total = parsed["total_amount_paise"]
            else: inv_total = inv_qty * inv_unit_price

        res = InvoiceAgent.match_invoice(
            po_id=po["id"],
            pr_id=current_pr["id"],
            invoice_number=inv_num,
            vendor_name=inv_vendor,
            item_name=inv_item,
            quantity=inv_qty,
            unit_price_paise=inv_unit_price,
            total_amount_paise=inv_total,
            file_path=file_path,
            original_filename=orig_filename,
            actor_name=session.get("user_name", "User")
        )

        conn.close()
        if res.get("match_status") == "matched":
            flash("Invoice 3-Way Match Verified! 0% unauthorized variance.", "success")
        else:
            flash(f"Invoice Exception Detected: {res['match_details']['explanation']}", "error")
        return redirect(url_for("agent_invoice_matching", po_id=po_id, pr_id=pr_id))

    # Load latest invoice
    invoice = None
    match_analysis = {}
    if po:
        inv_row = conn.execute("SELECT * FROM invoices WHERE po_id = ? ORDER BY created_at DESC", (po["id"],)).fetchone()
        if inv_row:
            invoice = dict(inv_row)
            invoice["formatted_total"] = format_inr(invoice["total_amount_paise"])
            if invoice.get("match_details"):
                match_analysis = json.loads(invoice["match_details"])

    conn.close()
    return render_template("agent_invoice_matching.html", active_page="agent_invoice_matching", po=po, current_pr=current_pr, vendor=vendor, grn=grn, invoice=invoice, match_analysis=match_analysis)

# Agent 6: Approval and Payment
@app.route("/agents/approval-payment", methods=["GET", "POST"])
@login_required
def agent_approval_payment():
    conn = get_db_connection()
    pr_id = request.form.get("pr_id") or request.args.get("pr_id")
    if not pr_id:
        latest = conn.execute("SELECT id FROM purchase_requests ORDER BY created_at DESC LIMIT 1").fetchone()
        pr_id = latest["id"] if latest else None

    if request.method == "POST":
        invoice_id = request.form.get("invoice_id")
        po_id = request.form.get("po_id")
        payment_method = request.form.get("payment_method", "Online")
        sim_fail = bool(request.form.get("simulate_failure"))

        res = ApprovalPaymentAgent.execute_payment(
            invoice_id=invoice_id,
            po_id=po_id,
            pr_id=pr_id,
            payment_method=payment_method,
            simulate_failure=sim_fail,
            actor_name=session.get("user_name", "User")
        )

        conn.close()
        if res.get("success"):
            flash(f"Payment executed successfully via {payment_method}! Reference: {res.get('transaction_reference')}", "success")
        else:
            flash(f"Payment failed / blocked: {res.get('message') or res.get('error')}", "error")
        return redirect(url_for("agent_approval_payment", pr_id=pr_id))

    current_pr = None
    po = None
    invoice = None
    payment = None
    purchase_approval = None
    grn = None

    if pr_id:
        pr_row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        if pr_row:
            current_pr = dict(pr_row)

            po_row = conn.execute("SELECT * FROM purchase_orders WHERE pr_id = ?", (pr_id,)).fetchone()
            if po_row:
                po = dict(po_row)
                po["formatted_total"] = format_inr(po["total_amount_paise"])

                g_row = conn.execute("SELECT * FROM goods_receipts WHERE po_id = ? ORDER BY received_at DESC", (po["id"],)).fetchone()
                grn = dict(g_row) if g_row else None

                inv_row = conn.execute("SELECT * FROM invoices WHERE po_id = ? ORDER BY created_at DESC", (po["id"],)).fetchone()
                if inv_row:
                    invoice = dict(inv_row)
                    invoice["formatted_total"] = format_inr(invoice["total_amount_paise"])

            app_row = conn.execute("""
                SELECT a.*, u.full_name as approver_name FROM purchase_approvals a
                JOIN users u ON a.approver_id = u.id WHERE a.pr_id = ? ORDER BY a.decided_at DESC
            """, (pr_id,)).fetchone()
            purchase_approval = dict(app_row) if app_row else None

            # Prioritize completed payments
            pay_row = conn.execute("SELECT * FROM payments WHERE pr_id = ? ORDER BY CASE WHEN status = 'completed' THEN 1 ELSE 2 END, processed_at DESC", (pr_id,)).fetchone()
            if pay_row:
                payment = dict(pay_row)
                payment["formatted_amount"] = format_inr(payment["amount_paise"])

    conn.close()
    return render_template("agent_approval_payment.html", active_page="agent_approval_payment", current_pr=current_pr, po=po, invoice=invoice, payment=payment, purchase_approval=purchase_approval, grn=grn)

# Dedicated Paid Receipt View & Download Route
@app.route("/receipt/<payment_id>")
@login_required
def view_payment_receipt(payment_id):
    conn = get_db_connection()
    pay_row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not pay_row:
        conn.close()
        flash("Payment receipt record not found.", "error")
        return redirect(url_for("dashboard"))

    payment = dict(pay_row)
    payment["formatted_amount"] = format_inr(payment["amount_paise"])

    inv_row = conn.execute("SELECT * FROM invoices WHERE id = ?", (payment["invoice_id"],)).fetchone()
    invoice = dict(inv_row) if inv_row else {}
    if invoice.get("total_amount_paise"):
        invoice["formatted_total"] = format_inr(invoice["total_amount_paise"])
    if invoice.get("unit_price_paise"):
        invoice["formatted_unit_price"] = format_inr(invoice["unit_price_paise"])

    po_row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (payment["po_id"],)).fetchone()
    po = dict(po_row) if po_row else {}
    if po.get("unit_price_paise"):
        po["formatted_unit_price"] = format_inr(po["unit_price_paise"])
    if po.get("total_amount_paise"):
        po["formatted_total"] = format_inr(po["total_amount_paise"])

    pr_row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (payment["pr_id"],)).fetchone()
    pr = dict(pr_row) if pr_row else {}

    vendor = {}
    if po.get("vendor_id"):
        v_row = conn.execute("SELECT * FROM vendors WHERE id = ?", (po["vendor_id"],)).fetchone()
        if v_row:
            vendor = dict(v_row)

    conn.close()
    return render_template("payment_receipt.html", payment=payment, invoice=invoice, po=po, current_pr=pr, vendor=vendor)

# ==============================================================================
# APPROVAL CENTER & AUDIT LOGS
# ==============================================================================
@app.route("/approvals", methods=["GET", "POST"])
@login_required
def approvals():
    conn = get_db_connection()

    if request.method == "POST":
        # Approval access granted to all authenticated users
        pr_id = request.form.get("pr_id")
        decision = request.form.get("decision")
        comment = request.form.get("comment", "").strip()
        return_url = request.form.get("return_url") or url_for("agent_budget_policy", pr_id=pr_id)

        if decision not in ["approved", "rejected"]:
            flash("Invalid decision.", "error")
            conn.close()
            return redirect(return_url)

        conn.execute("""
            INSERT INTO purchase_approvals (pr_id, approver_id, decision, comment)
            VALUES (?, ?, ?, ?)
        """, (pr_id, session.get("user_id"), decision, comment))

        new_status = "purchase_approved" if decision == "approved" else "purchase_rejected"
        conn.execute("UPDATE purchase_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_status, pr_id))
        conn.commit()

        AuditService.log_event(
            action=f"PURCHASE_{decision.upper()}",
            stage="Governance Approvals",
            actor=session.get("user_name", "User"),
            request_id=pr_id,
            details={"decision": decision, "comment": comment, "role": session.get("user_role", "user")}
        )

        flash(f"Purchase Request {pr_id} has been {decision} successfully.", "success")
        conn.close()
        return redirect(return_url)

    # Fetch Pending Approvals
    target_pr_id = request.args.get("pr_id")
    pending_rows = conn.execute("""
        SELECT pr.*, v.name as vendor_name, v.delivery_days, (pr.quantity * v.unit_price_paise) as total_cost_calc
        FROM purchase_requests pr
        LEFT JOIN vendors v ON pr.selected_vendor_id = v.id
        WHERE pr.status = 'pending_purchase_approval'
        ORDER BY pr.created_at DESC
    """).fetchall()

    pending_prs = []
    for r in pending_rows:
        d = dict(r)
        d["formatted_budget"] = format_inr(d["max_budget_paise"])
        d["formatted_total"] = format_inr(d["total_cost_calc"] or d["max_budget_paise"])
        pending_prs.append(d)

    # If target_pr_id is specified, sort it to the very top
    if target_pr_id:
        pending_prs.sort(key=lambda x: 0 if x["id"] == target_pr_id else 1)

    # Fetch Past Approvals
    past_rows = conn.execute("""
        SELECT a.*, u.full_name as approver_name, u.role as approver_role
        FROM purchase_approvals a
        JOIN users u ON a.approver_id = u.id
        ORDER BY a.decided_at DESC LIMIT 20
    """).fetchall()

    past_approvals = [dict(r) for r in past_rows]
    conn.close()

    return render_template("approvals.html", active_page="approvals", pending_prs=pending_prs, past_approvals=past_approvals, target_pr_id=target_pr_id)

@app.route("/audit-logs")
@login_required
def audit_logs():
    req_id = request.args.get("request_id")
    if req_id:
        logs = AuditService.get_for_request(req_id)
    else:
        logs = AuditService.get_recent(limit=100)
    return render_template("audit_logs.html", active_page="audit_logs", logs=logs)

# ==============================================================================
# PRINTABLE & DOWNLOADABLE PO
# ==============================================================================
@app.route("/purchase-orders/<po_id>/print")
@login_required
def print_po(po_id):
    conn = get_db_connection()
    po_row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
    if not po_row:
        conn.close()
        abort(404, description="PO not found")

    po = dict(po_row)
    po["formatted_unit_price"] = format_inr(po["unit_price_paise"])
    po["formatted_total"] = format_inr(po["total_amount_paise"])

    pr_row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (po["pr_id"],)).fetchone()
    pr = dict(pr_row) if pr_row else {}

    v_row = conn.execute("SELECT * FROM vendors WHERE id = ?", (po["vendor_id"],)).fetchone()
    vendor = dict(v_row) if v_row else {}
    conn.close()

    return render_template("po_view.html", po=po, pr=pr, vendor=vendor)

@app.route("/purchase-orders/<po_id>/pdf")
@login_required
def download_po_pdf(po_id):
    conn = get_db_connection()
    po = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
    if not po:
        conn.close()
        abort(404)

    pr = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (po["pr_id"],)).fetchone()
    vendor = conn.execute("SELECT * FROM vendors WHERE id = ?", (po["vendor_id"],)).fetchone()
    conn.close()

    pdf_filename = f"{po_id}.pdf"
    pdf_path = os.path.join(UPLOAD_FOLDER, pdf_filename)

    c = canvas.Canvas(pdf_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, 750, "PROCURE TOWER ENTERPRISE CORP")
    c.setFont("Helvetica", 10)
    c.drawString(50, 735, "Official Enterprise Purchase Order")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(400, 750, f"PO #: {po_id}")
    c.setFont("Helvetica", 10)
    c.drawString(400, 735, f"Date: {po['created_at']}")

    c.line(50, 720, 550, 720)
    c.drawString(50, 700, f"Vendor: {vendor['name']}")
    c.drawString(50, 685, f"Contact: {vendor['contact_email']}")
    c.drawString(320, 700, f"Department: {pr['department']}")
    c.drawString(320, 685, f"Requisition Ref: {pr['id']}")

    c.line(50, 665, 550, 665)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, 650, "Item Description")
    c.drawString(320, 650, "Quantity")
    c.drawString(390, 650, "Unit Price")
    c.drawString(480, 650, "Total")
    c.line(50, 640, 550, 640)

    c.setFont("Helvetica", 10)
    c.drawString(50, 620, f"{po['item_name']} ({vendor['product_name']})")
    c.drawString(320, 620, str(po["quantity"]))
    c.drawString(390, 620, format_inr(po["unit_price_paise"]))
    c.drawString(480, 620, format_inr(po["total_amount_paise"]))

    c.line(50, 600, 550, 600)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(390, 580, "Grand Total:")
    c.drawString(480, 580, format_inr(po["total_amount_paise"]))

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 520, f"Delivery Terms: {po['delivery_terms']}")
    c.drawString(50, 505, f"Payment Terms: {po['payment_terms']}")
    c.drawString(50, 480, "Authorized Signatory: Anita Sharma, IT Department Manager")
    c.save()

    return send_file(pdf_path, as_attachment=True, download_name=pdf_filename)

# ==============================================================================
# SAMPLE DOWNLOAD ROUTE
# ==============================================================================
@app.route("/samples/download/<filename>")
@login_required
def download_sample_invoice(filename):
    safe_name = secure_filename(filename)
    path = os.path.join(SAMPLES_FOLDER, safe_name)
    if os.path.exists(path):
        return send_file(path, as_attachment=True)
    abort(404, description="Sample invoice not found.")

# ==============================================================================
# DELETE ROUTES
# ==============================================================================
@app.route("/pr/delete/<pr_id>", methods=["POST"])
@login_required
def delete_purchase_request(pr_id):
    """Delete a purchase request and all related records from the dashboard."""
    conn = get_db_connection()
    try:
        # Delete child records first (in dependency order)
        conn.execute("DELETE FROM payments WHERE pr_id = ?", (pr_id,))
        conn.execute("DELETE FROM payment_approvals WHERE pr_id = ?", (pr_id,))
        conn.execute("DELETE FROM purchase_approvals WHERE pr_id = ?", (pr_id,))
        # Delete invoices linked to POs of this PR
        conn.execute("DELETE FROM invoices WHERE po_id IN (SELECT id FROM purchase_orders WHERE pr_id = ?)", (pr_id,))
        # Delete goods receipts linked to POs of this PR
        conn.execute("DELETE FROM goods_receipts WHERE po_id IN (SELECT id FROM purchase_orders WHERE pr_id = ?)", (pr_id,))
        conn.execute("DELETE FROM purchase_orders WHERE pr_id = ?", (pr_id,))
        conn.execute("DELETE FROM workflow_checkpoints WHERE request_id = ?", (pr_id,))
        conn.execute("DELETE FROM audit_logs WHERE request_id = ?", (pr_id,))
        conn.execute("DELETE FROM purchase_requests WHERE id = ?", (pr_id,))
        conn.commit()
        flash(f"Purchase request {pr_id} deleted successfully.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Error deleting request: {str(e)}", "error")
    finally:
        conn.close()
    return redirect(url_for("dashboard"))

@app.route("/approvals/audit/delete/<int:audit_id>", methods=["POST"])
@login_required
def delete_audit_record(audit_id):
    """Delete an audit history record from the Approval Center."""
    conn = get_db_connection()
    conn.execute("DELETE FROM purchase_approvals WHERE id = ?", (audit_id,))
    conn.commit()
    conn.close()
    flash("Audit record deleted.", "success")
    return redirect(url_for("approvals"))

# ==============================================================================
# APPLICATION BOOTSTRAP
# ==============================================================================
def bootstrap():
    init_db()
    # Pre-generate 3 sample invoices for instant testing
    try:
        generate_sample_invoice_pdf("Sample_Perfect_Match.pdf", "PO-DEMO-001", "INV-TECH-2026-001", "TechNova Solutions", "Enterprise Laptops", 500, 48000.0)
        generate_sample_invoice_pdf("Sample_Price_Mismatch.pdf", "PO-DEMO-001", "INV-TECH-MISPRICE", "TechNova Solutions", "Enterprise Laptops", 500, 52000.0)
        generate_sample_invoice_pdf("Sample_Qty_Mismatch.pdf", "PO-DEMO-001", "INV-TECH-MISQTY", "TechNova Solutions", "Enterprise Laptops", 550, 48000.0)
    except Exception as e:
        print(f"Sample PDF generation notice: {e}")

bootstrap()

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "True").lower() in ("true", "1")
    print("=" * 70)
    print(">>> AGENTIC PROCUREMENT-TO-PAYMENT CONTROL TOWER")
    print(f">> Server running at http://127.0.0.1:{port}")
    print(f">> Pre-seeded Test Accounts:")
    print("   - Manager:  manager@procurecorp.com  /  Manager@123")
    print("   - Employee: employee@procurecorp.com /  Employee@123")
    print("   - Admin:    admin@procurecorp.com    /  Admin@123")
    print("=" * 70)
    app.run(host="0.0.0.0", port=port, debug=debug)
