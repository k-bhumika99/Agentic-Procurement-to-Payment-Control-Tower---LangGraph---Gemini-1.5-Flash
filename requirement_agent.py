import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from database import get_db_connection, rupees_to_paise
from services.audit_service import AuditService

class RequirementAgent:
    """
    Agent 1: Requirement Analysis
    Converts natural language and form inputs into structured procurement requirements.
    Supports deterministic rule-based NLP extraction and optional LLM parsing.
    """

    @classmethod
    def parse_natural_language(cls, text: str) -> Dict[str, Any]:
        """
        Deterministic parser for purchase requests such as:
        "We need 500 laptops with at least 16 GB RAM, 512 GB SSD, delivery within 10 days, and a budget of ₹2.5 crore."
        """
        extracted = {
            "item_name": "Enterprise Laptops",
            "quantity": 1,
            "min_ram_gb": 16,
            "min_storage_gb": 512,
            "delivery_deadline_days": 14,
            "max_budget_paise": 0,
            "specs": text,
            "additional_requirements": ""
        }

        # 1. Quantity extraction (e.g. 500 laptops, 50 units, qty: 100)
        qty_match = re.search(r"(\d+)\s*(?:laptops?|units?|items?|computers?|notebooks?|machines?|pcs?)", text, re.IGNORECASE)
        if qty_match:
            extracted["quantity"] = int(qty_match.group(1))
        else:
            qty_match_alt = re.search(r"(?:quantity|qty|need)[:\s]*(\d+)", text, re.IGNORECASE)
            if qty_match_alt:
                extracted["quantity"] = int(qty_match_alt.group(1))

        # 2. RAM extraction (e.g. 16 GB RAM, 32GB)
        ram_match = re.search(r"(\d+)\s*GB\s*(?:RAM|Memory)?", text, re.IGNORECASE)
        if ram_match:
            extracted["min_ram_gb"] = int(ram_match.group(1))

        # 3. Storage extraction (e.g. 512 GB SSD, 1 TB SSD)
        tb_match = re.search(r"(\d+)\s*TB\s*(?:SSD|Storage|NVMe|Disk)?", text, re.IGNORECASE)
        if tb_match:
            extracted["min_storage_gb"] = int(tb_match.group(1)) * 1024
        else:
            storage_match = re.search(r"(\d+)\s*GB\s*(?:SSD|Storage|NVMe|Disk)?", text, re.IGNORECASE)
            if storage_match:
                extracted["min_storage_gb"] = int(storage_match.group(1))

        # 4. Delivery days (e.g. within 10 days, 7 days)
        days_match = re.search(r"(?:within|in|delivery)\s*(\d+)\s*days?", text, re.IGNORECASE)
        if days_match:
            extracted["delivery_deadline_days"] = int(days_match.group(1))

        # 5. Budget extraction: e.g. "₹2.5 crore", "2.5 Cr", "Rs. 50 Lakhs", "₹25000000"
        crore_match = re.search(r"[₹Rs\.]*\s*(\d+(?:\.\d+)?)\s*(?:crore|cr)", text, re.IGNORECASE)
        if crore_match:
            crores = float(crore_match.group(1))
            rupees = crores * 10000000  # 1 Crore = 10,000,000 INR
            extracted["max_budget_paise"] = rupees_to_paise(rupees)
        else:
            lakh_match = re.search(r"[₹Rs\.]*\s*(\d+(?:\.\d+)?)\s*(?:lakh|lac|l)", text, re.IGNORECASE)
            if lakh_match:
                lakhs = float(lakh_match.group(1))
                rupees = lakhs * 100000
                extracted["max_budget_paise"] = rupees_to_paise(rupees)
            else:
                num_budget = re.search(r"[₹Rs\.]*\s*(\d{5,12})", text, re.IGNORECASE)
                if num_budget:
                    extracted["max_budget_paise"] = rupees_to_paise(float(num_budget.group(1)))

        return extracted

    @classmethod
    def process_request(
        cls,
        title: str,
        department: str,
        item_name: str,
        quantity: int,
        specs: str,
        max_budget_paise: int,
        delivery_deadline_days: int,
        additional_requirements: str = "",
        user_id: Optional[int] = None,
        raw_prompt: str = "",
        min_ram_gb: int = 0,
        min_storage_gb: int = 0,
        actor_name: str = "Employee",
        db_path=None
    ) -> Dict[str, Any]:
        """
        Validates and records the initial purchase request into SQLite.
        """
        errors = []
        if not title or len(title.strip()) < 3:
            errors.append("Title must be at least 3 characters.")
        if not item_name or len(item_name.strip()) < 2:
            errors.append("Item name is required.")
        if quantity <= 0:
            errors.append("Quantity must be greater than zero.")
        if max_budget_paise <= 0:
            errors.append("Maximum budget must be greater than zero.")
        if delivery_deadline_days <= 0:
            errors.append("Delivery deadline must be at least 1 day.")

        if errors:
            return {
                "success": False,
                "errors": errors,
                "pr_id": None,
                "status": "validation_failed"
            }

        pr_id = f"PR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        
        conn = get_db_connection(db_path)
        conn.execute("""
            INSERT INTO purchase_requests (
                id, title, user_id, department, item_name, quantity, specs,
                min_ram_gb, min_storage_gb, max_budget_paise, delivery_deadline_days,
                additional_requirements, raw_prompt, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'requirements_analyzed')
        """, (
            pr_id, title.strip(), user_id, department.strip(), item_name.strip(), quantity,
            specs.strip(), min_ram_gb, min_storage_gb, max_budget_paise, delivery_deadline_days,
            additional_requirements.strip(), raw_prompt.strip()
        ))
        conn.commit()
        conn.close()

        AuditService.log_event(
            action="CREATE_PURCHASE_REQUEST",
            stage="Requirement Analysis",
            actor=actor_name,
            request_id=pr_id,
            details={
                "item": item_name,
                "quantity": quantity,
                "budget_paise": max_budget_paise,
                "delivery_days": delivery_deadline_days,
                "ram_gb": min_ram_gb,
                "storage_gb": min_storage_gb
            },
            db_path=db_path
        )

        return {
            "success": True,
            "pr_id": pr_id,
            "status": "requirements_analyzed",
            "message": f"Purchase Request {pr_id} successfully created and requirements structured."
        }
