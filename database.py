import sqlite3
import json
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash
from config import Config

def get_db_connection(db_path=None):
    if db_path is None:
        db_path = Config.DATABASE_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(db_path=None):
    if db_path is None:
        db_path = Config.DATABASE_PATH
        
    Path(Config.UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    
    # 1. Users table
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS department_budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department TEXT UNIQUE NOT NULL,
        total_budget_paise INTEGER NOT NULL,
        allocated_paise INTEGER NOT NULL DEFAULT 0,
        fiscal_year TEXT NOT NULL
    );
    """)
    
    # 5. Company Policies
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
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
    cursor.execute("""
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
    
    # 13. Workflow Checkpoints / State
    cursor.execute("""
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
    seed_initial_data(conn)
    conn.close()

def seed_initial_data(conn):
    cursor = conn.cursor()
    
    # Check if users already seeded
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        # Default test users
        users = [
            ("Admin User", "admin@procurecorp.com", generate_password_hash("Admin@123"), "Executive", "admin"),
            ("Anita Sharma (Manager)", "manager@procurecorp.com", generate_password_hash("Manager@123"), "IT", "manager"),
            ("Rajesh Patel (Employee)", "employee@procurecorp.com", generate_password_hash("Employee@123"), "IT", "employee")
        ]
        cursor.executemany("""
            INSERT INTO users (full_name, email, password_hash, department, role)
            VALUES (?, ?, ?, ?, ?)
        """, users)

    # Check if vendors already seeded
    cursor.execute("SELECT COUNT(*) FROM vendors")
    if cursor.fetchone()[0] == 0:
        # Realistic hardware vendors matching the prompt's 500 laptops example
        # Unit price in paise: e.g. Rs 48,000 = 4800000 paise
        vendors = [
            (
                "TechNova Solutions", "Laptops & Hardware", "TechNova ThinkBook Pro",
                "Intel Core i7 13th Gen, 16 GB DDR5 RAM, 512 GB NVMe SSD, 14-inch FHD IPS",
                16, 512, 4800000, 7, 3, 4.8, "sales@technova-solutions.com"
            ),
            (
                "Apex Digital Corp", "Laptops & Hardware", "Apex EliteBook 840 G10",
                "Intel Core i5 13th Gen, 16 GB DDR5 RAM, 512 GB SSD, 14-inch FHD",
                16, 512, 4700000, 14, 2, 4.3, "enterprise@apexdigital.com"
            ),
            (
                "Zenith Systems India", "Laptops & Hardware", "Zenith OfficeBook Lite",
                "Intel Core i3 12th Gen, 8 GB RAM, 256 GB SSD, 15.6-inch HD",
                8, 256, 3800000, 5, 1, 4.0, "support@zenithindia.com"
            ),
            (
                "PrimeByte Technologies", "Laptops & Hardware", "Prime Latitude Workstation",
                "Intel Core i7 13th Gen, 16 GB RAM, 512 GB SSD, 14-inch FHD Anti-Glare",
                16, 512, 4900000, 9, 3, 4.6, "b2b@primebyte.com"
            ),
            (
                "CloudVanguard Infotech", "Laptops & Hardware", "Vanguard Developer Pro Max",
                "Apple M3 Pro / 32 GB Unified Memory, 1 TB SSD, 14-inch Liquid Retina",
                32, 1024, 8500000, 8, 3, 4.9, "enterprise@cloudvanguard.com"
            )
        ]
        cursor.executemany("""
            INSERT INTO vendors (name, category, product_name, product_specs, ram_gb, storage_gb, unit_price_paise, delivery_days, warranty_years, rating, contact_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, vendors)

    # Check if department budgets already seeded
    cursor.execute("SELECT COUNT(*) FROM department_budgets")
    if cursor.fetchone()[0] == 0:
        # IT budget: Rs 5 Crore = 5,00,00,000 INR = 500,00,00,000 paise (5 billion paise)
        budgets = [
            ("IT", 5000000000, 0, "FY 2026-27"),
            ("Operations", 2000000000, 0, "FY 2026-27"),
            ("Human Resources", 500000000, 0, "FY 2026-27"),
            ("Finance", 1000000000, 0, "FY 2026-27"),
            ("Marketing", 800000000, 0, "FY 2026-27")
        ]
        cursor.executemany("""
            INSERT INTO department_budgets (department, total_budget_paise, allocated_paise, fiscal_year)
            VALUES (?, ?, ?, ?)
        """, budgets)

    # Check if company policies already seeded
    cursor.execute("SELECT COUNT(*) FROM company_policies")
    if cursor.fetchone()[0] == 0:
        policies = [
            ("TIER_1_STANDARD", "Standard Low-Value Purchase", 5000000, "Purchases under Rs 50,000 do not require executive approval.", 0, 0),
            ("TIER_2_MANAGER", "Manager Approval Threshold", 5000000, "Purchases between Rs 50,000 and Rs 50,00,000 require Department Manager approval.", 1, 0),
            ("TIER_3_EXECUTIVE", "Executive Board Approval Threshold", 500000000, "Purchases exceeding Rs 50 Lakhs (Rs 50,00,000) require VP/Executive approval.", 1, 1),
            ("THREE_WAY_MATCH", "Strict 3-Way Matching", 0, "Invoice, Purchase Order, and Goods Receipt quantities & prices must match with 0% unauthorized variance.", 1, 0)
        ]
        cursor.executemany("""
            INSERT INTO company_policies (policy_key, name, threshold_paise, description, requires_manager_approval, requires_vp_approval)
            VALUES (?, ?, ?, ?, ?, ?)
        """, policies)

    conn.commit()

# Currency formatting helpers
def paise_to_rupees(paise: int) -> float:
    return (paise or 0) / 100.0

def rupees_to_paise(rupees: float) -> int:
    return int(round((rupees or 0.0) * 100))

def format_inr(paise: int) -> str:
    """Format paise into Indian numbering system INR string (e.g., Rs. 2,50,00,000.00)."""
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
        # Group by 2 digits from the right
        groups = []
        while remaining:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        formatted = ",".join(groups) + "," + last_three
        
    res = f"₹{formatted}.{decimal_part}"
    return f"-{res}" if is_negative else res

def format_inr_short(paise: int) -> str:
    """Format large amounts in readable Lakhs/Crores (e.g. ₹2.5 Cr)."""
    if paise is None:
        return "₹0"
    rupees = paise / 100.0
    if abs(rupees) >= 10000000: # 1 Crore = 10,000,000
        return f"₹{rupees / 10000000:.2f} Cr"
    elif abs(rupees) >= 100000: # 1 Lakh = 100,000
        return f"₹{rupees / 100000:.2f} L"
    else:
        return format_inr(paise)
