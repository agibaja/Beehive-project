import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client


BACKEND_DIR = Path(__file__).resolve().parents[1]

load_dotenv(BACKEND_DIR / "optimizer" / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_KEY en optimizer/.env."
    )


def get_database():
    return create_client(SUPABASE_URL, SUPABASE_KEY)