from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
from database import get_db_connection, format_inr, paise_to_rupees

@dataclass
class User:
    id: int
    full_name: str
    email: str
    department: str
    role: str
    password_hash: str = ""
    created_at: str = ""

    @classmethod
    def get_by_id(cls, user_id: int, db_path=None) -> Optional["User"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if row:
            return cls(
                id=row["id"],
                full_name=row["full_name"],
                email=row["email"],
                department=row["department"],
                role=row["role"],
                password_hash=row["password_hash"],
                created_at=str(row["created_at"])
            )
        return None

    @classmethod
    def get_by_email(cls, email: str, db_path=None) -> Optional["User"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email.strip(),)).fetchone()
        conn.close()
        if row:
            return cls(
                id=row["id"],
                full_name=row["full_name"],
                email=row["email"],
                department=row["department"],
                role=row["role"],
                password_hash=row["password_hash"],
                created_at=str(row["created_at"])
            )
        return None

    @classmethod
    def create(cls, full_name: str, email: str, password_hash: str, department: str, role: str = "employee", db_path=None) -> int:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (full_name, email, password_hash, department, role)
            VALUES (?, ?, ?, ?, ?)
        """, (full_name.strip(), email.strip().lower(), password_hash, department.strip(), role))
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        return user_id

@dataclass
class PurchaseRequest:
    id: str
    title: str
    user_id: Optional[int]
    department: str
    item_name: str
    quantity: int
    specs: str
    min_ram_gb: int
    min_storage_gb: int
    max_budget_paise: int
    delivery_deadline_days: int
    additional_requirements: str
    raw_prompt: str
    status: str
    selected_vendor_id: Optional[int]
    created_at: str
    updated_at: str

    @property
    def formatted_budget(self) -> str:
        return format_inr(self.max_budget_paise)

    @classmethod
    def get_by_id(cls, pr_id: str, db_path=None) -> Optional["PurchaseRequest"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM purchase_requests WHERE id = ?", (pr_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

    @classmethod
    def list_all(cls, limit: int = 50, db_path=None) -> List["PurchaseRequest"]:
        conn = get_db_connection(db_path)
        rows = conn.execute("SELECT * FROM purchase_requests ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [cls(**dict(r)) for r in rows]

    @classmethod
    def update_status(cls, pr_id: str, status: str, db_path=None):
        conn = get_db_connection(db_path)
        conn.execute("UPDATE purchase_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, pr_id))
        conn.commit()
        conn.close()

    @classmethod
    def set_selected_vendor(cls, pr_id: str, vendor_id: int, db_path=None):
        conn = get_db_connection(db_path)
        conn.execute("UPDATE purchase_requests SET selected_vendor_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (vendor_id, pr_id))
        conn.commit()
        conn.close()

@dataclass
class Vendor:
    id: int
    name: str
    category: str
    product_name: str
    product_specs: str
    ram_gb: int
    storage_gb: int
    unit_price_paise: int
    delivery_days: int
    warranty_years: int
    rating: float
    contact_email: str
    is_active: int

    @property
    def formatted_unit_price(self) -> str:
        return format_inr(self.unit_price_paise)

    @classmethod
    def get_by_id(cls, vendor_id: int, db_path=None) -> Optional["Vendor"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

    @classmethod
    def list_active(cls, db_path=None) -> List["Vendor"]:
        conn = get_db_connection(db_path)
        rows = conn.execute("SELECT * FROM vendors WHERE is_active = 1 ORDER BY rating DESC").fetchall()
        conn.close()
        return [cls(**dict(r)) for r in rows]

@dataclass
class PurchaseOrder:
    id: str
    pr_id: str
    vendor_id: int
    item_name: str
    quantity: int
    unit_price_paise: int
    total_amount_paise: int
    delivery_terms: str
    payment_terms: str
    status: str
    created_by: Optional[int]
    created_at: str

    @property
    def formatted_unit_price(self) -> str:
        return format_inr(self.unit_price_paise)

    @property
    def formatted_total(self) -> str:
        return format_inr(self.total_amount_paise)

    @classmethod
    def get_by_id(cls, po_id: str, db_path=None) -> Optional["PurchaseOrder"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

    @classmethod
    def get_by_pr_id(cls, pr_id: str, db_path=None) -> Optional["PurchaseOrder"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM purchase_orders WHERE pr_id = ?", (pr_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

    @classmethod
    def list_all(cls, db_path=None) -> List["PurchaseOrder"]:
        conn = get_db_connection(db_path)
        rows = conn.execute("SELECT * FROM purchase_orders ORDER BY created_at DESC").fetchall()
        conn.close()
        return [cls(**dict(r)) for r in rows]

@dataclass
class GoodsReceipt:
    id: str
    po_id: str
    item_name: str
    quantity_ordered: int
    quantity_received: int
    inspection_status: str
    notes: str
    received_at: str

    @classmethod
    def get_by_po_id(cls, po_id: str, db_path=None) -> Optional["GoodsReceipt"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM goods_receipts WHERE po_id = ?", (po_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

@dataclass
class Invoice:
    id: str
    po_id: str
    vendor_name: str
    invoice_number: str
    invoice_date: str
    item_name: str
    quantity: int
    unit_price_paise: int
    total_amount_paise: int
    file_path: str
    original_filename: str
    match_status: str
    match_details: str
    created_at: str

    @property
    def formatted_total(self) -> str:
        return format_inr(self.total_amount_paise)

    @classmethod
    def get_by_id(cls, invoice_id: str, db_path=None) -> Optional["Invoice"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

    @classmethod
    def get_by_po_id(cls, po_id: str, db_path=None) -> Optional["Invoice"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM invoices WHERE po_id = ? ORDER BY created_at DESC", (po_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

@dataclass
class Payment:
    id: str
    invoice_id: str
    po_id: str
    pr_id: str
    amount_paise: int
    status: str
    transaction_reference: str
    idempotency_key: str
    failure_reason: str
    processed_at: str

    @property
    def formatted_amount(self) -> str:
        return format_inr(self.amount_paise)

    @classmethod
    def get_by_id(cls, payment_id: str, db_path=None) -> Optional["Payment"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None

    @classmethod
    def get_by_pr_id(cls, pr_id: str, db_path=None) -> Optional["Payment"]:
        conn = get_db_connection(db_path)
        row = conn.execute("SELECT * FROM payments WHERE pr_id = ?", (pr_id,)).fetchone()
        conn.close()
        if row:
            return cls(**dict(row))
        return None
