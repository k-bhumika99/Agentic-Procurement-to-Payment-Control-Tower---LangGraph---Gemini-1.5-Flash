from typing import Dict, Any, List, Optional
from database import get_db_connection, format_inr
from models import PurchaseRequest, Vendor
from services.audit_service import AuditService

class VendorAgent:
    """
    Agent 2: Vendor Comparison
    Retrieves purchase requirements automatically, evaluates active market vendors,
    filters by mandatory technical and SLA constraints, calculates exact total costs,
    and recommends the highest-ranking compliant supplier with transparent explanations.
    """

    @classmethod
    def compare_vendors(cls, pr_id: str, actor_name: str = "System", auto_select: bool = True, db_path=None) -> Dict[str, Any]:
        pr = PurchaseRequest.get_by_id(pr_id, db_path=db_path)
        if not pr:
            return {"success": False, "error": f"Purchase Request {pr_id} not found."}

        conn = get_db_connection(db_path)
        vendor_rows = conn.execute("SELECT * FROM vendors WHERE is_active = 1").fetchall()
        conn.close()

        if not vendor_rows:
            return {"success": False, "error": "No active vendors available in database."}

        comparison_results = []
        eligible_vendors = []

        for row in vendor_rows:
            v = dict(row)
            rejection_reasons = []

            # 1. Mandatory RAM check
            if pr.min_ram_gb > 0 and v.get("ram_gb", 0) < pr.min_ram_gb:
                rejection_reasons.append(f"RAM below required specification ({v.get('ram_gb', 0)} GB < {pr.min_ram_gb} GB required).")

            # 2. Mandatory Storage check
            if pr.min_storage_gb > 0 and v.get("storage_gb", 0) < pr.min_storage_gb:
                rejection_reasons.append(f"Storage below required specification ({v.get('storage_gb', 0)} GB < {pr.min_storage_gb} GB required).")

            # 3. Delivery SLA check
            if pr.delivery_deadline_days > 0 and v["delivery_days"] > pr.delivery_deadline_days:
                rejection_reasons.append(f"Delivery timeline exceeds requirement ({v['delivery_days']} days > {pr.delivery_deadline_days} days max).")

            # 4. Total Cost Calculation (Paise precision in Python)
            total_cost_paise = pr.quantity * v["unit_price_paise"]

            # 5. Budget Cap check
            if pr.max_budget_paise > 0 and total_cost_paise > pr.max_budget_paise:
                rejection_reasons.append(f"Total cost exceeds maximum approved budget ({format_inr(total_cost_paise)} > {format_inr(pr.max_budget_paise)}).")

            is_eligible = len(rejection_reasons) == 0

            # Scoring algorithm for eligible vendors:
            # Score = (1 / total_cost) normalized + rating * 20 + warranty * 10
            # Transparent policy: Lowest compliant total cost gets highest commercial priority, weighted by quality rating & warranty.
            vendor_entry = {
                "id": v["id"],
                "name": v["name"],
                "product_name": v["product_name"],
                "product_specs": v["product_specs"],
                "ram_gb": v.get("ram_gb", 0),
                "storage_gb": v.get("storage_gb", 0),
                "unit_price_paise": v["unit_price_paise"],
                "formatted_unit_price": format_inr(v["unit_price_paise"]),
                "total_cost_paise": total_cost_paise,
                "formatted_total_cost": format_inr(total_cost_paise),
                "delivery_days": v["delivery_days"],
                "warranty_years": v["warranty_years"],
                "rating": v["rating"],
                "contact_email": v.get("contact_email", ""),
                "is_eligible": is_eligible,
                "rejection_reasons": rejection_reasons,
                "score": 0.0
            }

            if is_eligible:
                # Value calculation: 50 points base for price competitiveness + (rating / 5.0 * 30) + (warranty * 6.66)
                # Lower price gives higher score
                vendor_entry["score"] = round((v["rating"] * 10.0) + (v["warranty_years"] * 8.0) - (v["unit_price_paise"] / 1000000.0), 2)
                eligible_vendors.append(vendor_entry)

            comparison_results.append(vendor_entry)

        # Sort eligible vendors by total_cost_paise ascending (L1 vendor principle), then rating descending
        eligible_vendors.sort(key=lambda x: (x["total_cost_paise"], -x["rating"], -x["warranty_years"]))

        recommended_vendor = eligible_vendors[0] if eligible_vendors else None
        recommendation_explanation = ""

        if recommended_vendor:
            recommendation_explanation = (
                f"Recommended '{recommended_vendor['name']}' ({recommended_vendor['product_name']}) "
                f"as the best value compliant vendor. It meets all technical specs ({recommended_vendor['ram_gb']}GB RAM, {recommended_vendor['storage_gb']}GB SSD), "
                f"complies with the delivery timeline ({recommended_vendor['delivery_days']} days vs {pr.delivery_deadline_days} days max), "
                f"provides {recommended_vendor['warranty_years']} years warranty with a {recommended_vendor['rating']}★ supplier rating, "
                f"at a total procurement cost of {recommended_vendor['formatted_total_cost']} (within budget {pr.formatted_budget})."
            )
            if auto_select:
                PurchaseRequest.set_selected_vendor(pr_id, recommended_vendor["id"], db_path=db_path)
                PurchaseRequest.update_status(pr_id, "vendor_compared", db_path=db_path)
        else:
            recommendation_explanation = "No vendors met the mandatory specification, timeline, and budget constraints."

        AuditService.log_event(
            action="COMPARE_VENDORS",
            stage="Vendor Comparison",
            actor=actor_name,
            request_id=pr_id,
            details={
                "total_vendors_evaluated": len(vendor_rows),
                "eligible_count": len(eligible_vendors),
                "recommended_vendor_id": recommended_vendor["id"] if recommended_vendor else None,
                "recommended_vendor_name": recommended_vendor["name"] if recommended_vendor else None
            },
            db_path=db_path
        )

        return {
            "success": True,
            "pr_id": pr_id,
            "total_evaluated": len(vendor_rows),
            "eligible_count": len(eligible_vendors),
            "vendors": comparison_results,
            "recommended_vendor": recommended_vendor,
            "explanation": recommendation_explanation
        }
