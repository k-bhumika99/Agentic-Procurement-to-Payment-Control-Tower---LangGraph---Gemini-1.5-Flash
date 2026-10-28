import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from database import get_db_connection

class PaymentService:
    """
    Simulated Corporate Banking & Payment Gateway API (e.g., RTGS / NEFT / Swift).
    Enforces idempotency, duplicate prevention, and generates verifiable transaction references.
    """

    @staticmethod
    def process_payment(
        invoice_id: str,
        po_id: str,
        pr_id: str,
        amount_paise: int,
        idempotency_key: str,
        simulate_failure: bool = False,
        db_path=None
    ) -> Dict[str, Any]:
        conn = get_db_connection(db_path)
        
        # 1. Idempotency check: Has this idempotency key already been used?
        existing_payment = conn.execute("""
            SELECT * FROM payments WHERE idempotency_key = ?
        """, (idempotency_key,)).fetchone()
        
        if existing_payment:
            conn.close()
            return {
                "success": existing_payment["status"] == "completed",
                "payment_id": existing_payment["id"],
                "status": existing_payment["status"],
                "transaction_reference": existing_payment["transaction_reference"],
                "idempotency_key": idempotency_key,
                "message": "Payment already processed previously (Idempotent response).",
                "duplicate": True
            }

        # 2. Duplicate invoice payment check: Has this invoice already been paid successfully?
        paid_invoice = conn.execute("""
            SELECT * FROM payments WHERE invoice_id = ? AND status = 'completed'
        """, (invoice_id,)).fetchone()
        
        if paid_invoice:
            conn.close()
            return {
                "success": False,
                "payment_id": paid_invoice["id"],
                "status": "blocked",
                "transaction_reference": paid_invoice["transaction_reference"],
                "idempotency_key": idempotency_key,
                "message": f"Duplicate payment blocked: Invoice {invoice_id} has already been settled under Transaction {paid_invoice['transaction_reference']}.",
                "duplicate": True
            }

        # 3. Simulate Payment Processing
        payment_id = f"PAY-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        
        if simulate_failure:
            failure_reason = "Simulated bank gateway timeout or insufficient escrow liquidity."
            conn.execute("""
                INSERT INTO payments (id, invoice_id, po_id, pr_id, amount_paise, status, transaction_reference, idempotency_key, failure_reason)
                VALUES (?, ?, ?, ?, ?, 'failed', NULL, ?, ?)
            """, (payment_id, invoice_id, po_id, pr_id, amount_paise, idempotency_key, failure_reason))
            conn.commit()
            conn.close()
            return {
                "success": False,
                "payment_id": payment_id,
                "status": "failed",
                "transaction_reference": None,
                "idempotency_key": idempotency_key,
                "message": failure_reason
            }

        # Successful transaction
        txn_ref = f"TXN-HDFC-RTGS-{datetime.now().strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6].upper()}"
        
        conn.execute("""
            INSERT INTO payments (id, invoice_id, po_id, pr_id, amount_paise, status, transaction_reference, idempotency_key, failure_reason)
            VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, NULL)
        """, (payment_id, invoice_id, po_id, pr_id, amount_paise, txn_ref, idempotency_key))
        
        # Update PO status to 'closed'
        conn.execute("UPDATE purchase_orders SET status = 'closed' WHERE id = ?", (po_id,))
        
        # Update PR status to 'payment_completed'
        conn.execute("UPDATE purchase_requests SET status = 'payment_completed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (pr_id,))
        
        conn.commit()
        conn.close()

        return {
            "success": True,
            "payment_id": payment_id,
            "status": "completed",
            "transaction_reference": txn_ref,
            "idempotency_key": idempotency_key,
            "message": "Payment executed successfully through simulated enterprise payment gateway."
        }
