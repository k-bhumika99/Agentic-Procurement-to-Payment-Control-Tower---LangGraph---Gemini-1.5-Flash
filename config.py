import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "procurement-control-tower-secret-key-2026-secure")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "procurement.db"))
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB max upload
    ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
    
    # Session security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    
    # LLM Optional Config
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
