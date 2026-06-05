import os 

from dotenv import load_dotenv

load_dotenv()

HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
MAX_PAGES: int = int(os.getenv("MAX_PAGES", "3"))
DELAY_MIN: float = float(os.getenv("DELAY_MIN", "2.5"))
DELAY_MAX: float = float(os.getenv("DELAY_MAX", "5.0"))
TIMEOUT_MS: int = int(os.getenv("TIMEOUT_MS", "30000"))