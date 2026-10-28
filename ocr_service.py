import re
import os
from typing import Dict, Any, Optional
from pathlib import Path
from pypdf import PdfReader
from PIL import Image

try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False

from database import rupees_to_paise

class OCRService:
    """
    Extracts text and key accounting fields from supplier invoices in PDF or image format.
    Supports native PDF text reading, pytesseract OCR, and structured pattern matching.
    """

    @classmethod
    def extract_text_from_file(cls, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            return ""
            
        ext = path.suffix.lower()
        extracted_text = ""

        # 1. PDF File extraction
        if ext == ".pdf":
            try:
                reader = PdfReader(str(path))
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"
            except Exception as e:
                extracted_text = f"Error reading PDF: {str(e)}"

        # 2. Image File extraction (PNG, JPG, JPEG)
        elif ext in [".png", ".jpg", ".jpeg"]:
            if PYTESSERACT_AVAILABLE:
                try:
                    img = Image.open(str(path))
                    extracted_text = pytesseract.image_to_string(img)
                except Exception as e:
                    extracted_text = f"OCR Error: {str(e)}"
            else:
                extracted_text = "PyTesseract not available on local system."

        return extracted_text.strip()

    @classmethod
    def parse_invoice_fields(cls, text: str, fallback_filename: str = "") -> Dict[str, Any]:
        """
        Parses text for standard commercial invoice structures:
        - Invoice Number
        - Purchase Order Reference
        - Vendor Name
        - Item Name
        - Quantity
        - Unit Price
        - Total Amount
        """
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

        # 1. Invoice Number pattern (e.g., INV-2026-001 or Invoice Number: INV-9812)
        inv_match = re.search(r"(?:invoice\s*(?:no|number|#)?[:\s]*)([A-Z0-9\-]+)", text, re.IGNORECASE)
        if inv_match:
            fields["invoice_number"] = inv_match.group(1).strip()
        else:
            # Look for general INV-xxxx pattern
            gen_inv = re.search(r"\b(INV-[A-Z0-9\-]+)\b", text, re.IGNORECASE)
            if gen_inv:
                fields["invoice_number"] = gen_inv.group(1).strip()

        # 2. PO Number pattern (e.g., PO-20261022-XXXX)
        po_match = re.search(r"(?:po\s*(?:no|number|#|ref)?[:\s]*)([A-Z0-9\-]+)", text, re.IGNORECASE)
        if po_match:
            fields["po_id"] = po_match.group(1).strip()
        else:
            gen_po = re.search(r"\b(PO-[A-Z0-9\-]+)\b", text, re.IGNORECASE)
            if gen_po:
                fields["po_id"] = gen_po.group(1).strip()

        # 3. Vendor Name pattern
        vendor_match = re.search(r"(?:vendor|supplier|from|seller)[:\s]*([^\n\r]+)", text, re.IGNORECASE)
        if vendor_match:
            fields["vendor_name"] = vendor_match.group(1).strip()
        else:
            # Check known vendors if mentioned in text
            known_vendors = ["TechNova Solutions", "Apex Digital Corp", "Zenith Systems India", "PrimeByte Technologies", "CloudVanguard Infotech"]
            for kv in known_vendors:
                if kv.lower() in text.lower():
                    fields["vendor_name"] = kv
                    break

        # 4. Quantity pattern
        qty_match = re.search(r"(?:quantity|qty)[:\s]*([0-9,]+)", text, re.IGNORECASE)
        if qty_match:
            try:
                fields["quantity"] = int(qty_match.group(1).replace(",", "").strip())
            except ValueError:
                pass

        # 5. Unit Price pattern
        unit_price_match = re.search(r"(?:unit\s*price|rate|price/unit)[:\s]*[₹Rs\.]*\s*([0-9,]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
        if unit_price_match:
            try:
                rupees = float(unit_price_match.group(1).replace(",", "").strip())
                fields["unit_price_paise"] = rupees_to_paise(rupees)
            except ValueError:
                pass

        # 6. Total Amount pattern
        total_match = re.search(r"(?:total\s*(?:amount)?|grand\s*total|invoice\s*total)[:\s]*[₹Rs\.]*\s*([0-9,]+(?:\.[0-9]{2})?)", text, re.IGNORECASE)
        if total_match:
            try:
                rupees = float(total_match.group(1).replace(",", "").strip())
                fields["total_amount_paise"] = rupees_to_paise(rupees)
            except ValueError:
                pass

        # 7. Date pattern
        date_match = re.search(r"(?:date)[:\s]*([0-9]{2,4}[\-\/][0-9]{1,2}[\-\/][0-9]{1,4})", text, re.IGNORECASE)
        if date_match:
            fields["invoice_date"] = date_match.group(1).strip()

        # 8. Item name pattern
        item_match = re.search(r"(?:item|description|product)[:\s]*([^\n\r]+)", text, re.IGNORECASE)
        if item_match:
            fields["item_name"] = item_match.group(1).strip()

        # If quantity and unit price exist but total doesn't, calculate
        if fields["quantity"] and fields["unit_price_paise"] and not fields["total_amount_paise"]:
            fields["total_amount_paise"] = fields["quantity"] * fields["unit_price_paise"]

        return fields
