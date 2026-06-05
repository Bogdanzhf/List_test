from __future__ import annotations

import random
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth import Stealth

from config import DELAY_MAX, DELAY_MIN, HEADLESS, TIMEOUT_MS

# Пул User-Agent'ов реальных браузеров (Windows + Mac, разные версии Chrome)
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3_1) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Реалистичные разрешения экранов
VIEWPORTS: list[dict[str, int]] = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 720},
]


async def human_delay() -> None:
    """Случайная пауза для имитации действий живого пользователя."""
    import asyncio

    delay = random.uniform(DELAY_MIN, DELAY_MAX)
    await asyncio.sleep(delay)


@asynccontextmanager
async def create_browser_context() -> AsyncGenerator[
    tuple[Browser, BrowserContext], None
]:
    """
    Запускает Playwright Chromium с настройками против детекции.
    Возвращает браузер и контекст. Закрывает оба при выходе.
    """
    user_agent = random.choice(USER_AGENTS)
    viewport = random.choice(VIEWPORTS)

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080",
            ],
        )

        context: BrowserContext = await browser.new_context(
            user_agent=user_agent,
            viewport=viewport,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            java_script_enabled=True,
            accept_downloads=False,
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
        )

        # Таймаут по умолчанию для всех действий в контексте
        context.set_default_timeout(TIMEOUT_MS)

        try:
            yield browser, context
        finally:
            await context.close()
            await browser.close()


async def create_stealth_page(context: BrowserContext) -> Page:
    """
    Создаёт новую вкладку и применяет стелс-патчи.
    Стелс скрывает признаки headless: navigator.webdriver, permissions и др.
    """
    page: Page = await context.new_page()
    await Stealth().apply_stealth_async(page)

    # Дополнительно переопределяем webdriver через CDP
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    """)

    return page
