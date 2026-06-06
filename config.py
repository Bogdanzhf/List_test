import os

from dotenv import load_dotenv

load_dotenv()


def _optional_env(key: str) -> str | None:
    value = os.getenv(key, "").strip()
    return value or None


# Основные параметры браузера
HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
MAX_PAGES: int = int(os.getenv("MAX_PAGES", "3"))
BROWSER_TYPE: str = os.getenv("BROWSER_TYPE", "chromium")

# Прокси (опционально): http://host:port или http://user:pass@host:port
PROXY_SERVER: str | None = _optional_env("PROXY_SERVER")

# Задержки между действиями (в секундах)
DELAY_MIN: float = float(os.getenv("DELAY_MIN", "1.5"))
DELAY_MAX: float = float(os.getenv("DELAY_MAX", "3.5"))

# Таймауты (в миллисекундах)
TIMEOUT_MS: int = int(os.getenv("TIMEOUT_MS", "30000"))
WAIT_SELECTOR_TIMEOUT_MS: int = int(os.getenv("WAIT_SELECTOR_TIMEOUT_MS", "5000"))
PRODUCT_SELECTOR_TIMEOUT_MS: int = int(
    os.getenv("PRODUCT_SELECTOR_TIMEOUT_MS", str(WAIT_SELECTOR_TIMEOUT_MS))
)

# Навигация: domcontentloaded быстрее и стабильнее для Ozon, чем networkidle
NAVIGATION_WAIT: str = os.getenv("NAVIGATION_WAIT", "domcontentloaded")

# Параметры retry при блокировке
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "2"))
RETRY_DELAY_MIN: float = float(os.getenv("RETRY_DELAY_MIN", "60"))
RETRY_DELAY_MAX: float = float(os.getenv("RETRY_DELAY_MAX", "120"))

# Ожидание решения капчи (секунды); проверка каждые CAPTCHA_CHECK_INTERVAL сек
CAPTCHA_WAIT_SECONDS: float = float(os.getenv("CAPTCHA_WAIT_SECONDS", "60"))
CAPTCHA_CHECK_INTERVAL: float = float(os.getenv("CAPTCHA_CHECK_INTERVAL", "5"))

# Параметры ожидания lazy loading
SCROLL_STABILIZE_THRESHOLD: int = int(os.getenv("SCROLL_STABILIZE_THRESHOLD", "3"))
SCROLL_MAX_ITERATIONS: int = int(os.getenv("SCROLL_MAX_ITERATIONS", "20"))
SCROLL_WAIT_TIME: float = float(os.getenv("SCROLL_WAIT_TIME", "1.5"))

# Лимит времени выполнения (0 = без ограничения)
MAX_EXECUTION_SECONDS: int = int(os.getenv("MAX_EXECUTION_SECONDS", "0"))
