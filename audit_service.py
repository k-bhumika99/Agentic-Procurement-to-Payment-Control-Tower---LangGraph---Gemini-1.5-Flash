from datetime import datetime
import json
from typing import Optional, List, Dict, Any
from database import get_db_connection

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
    def get_logs_for_request(request_id: str, db_path=None) -> List[Dict[str, Any]]:
        conn = get_db_connection(db_path)
        rows = conn.execute("""
            SELECT * FROM audit_logs WHERE request_id = ? ORDER BY created_at DESC
        """, (request_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_recent_logs(limit: int = 50, db_path=None) -> List[Dict[str, Any]]:
        conn = get_db_connection(db_path)
        rows = conn.execute("""
            SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
