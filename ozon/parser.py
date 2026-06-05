from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from playwright.async_api import Page

from config import DELAY_MAX, DELAY_MIN, MAX_PAGES, TIMEOUT_MS
from ozon.browser import create_browser_context, create_stealth_page, human_delay
from ozon.models import PositionResult

logger = logging.getLogger(__name__)

# SKU — число в конце пути URL перед слешем или параметрами
SKU_PATTERN = re.compile(r"-(\d{5,12})(?:/|\?|$)")

OZON_SEARCH_URL = (
    "https://www.ozon.ru/search/?text={query}&from_global=true&page={page}"
)


def extract_sku_from_href(href: str) -> Optional[str]:
    """Извлекает SKU из ссылки на товар Ozon."""
    match = SKU_PATTERN.search(href)
    return match.group(1) if match else None


async def scroll_page_fully(page: Page) -> None:
    """
    Прокручивает страницу вниз порциями — имитирует чтение.
    Нужно чтобы подгрузились все карточки (lazy loading).
    """
    scroll_step = 600
    current = 0

    page_height: int = await page.evaluate("document.body.scrollHeight")

    while current < page_height:
        current += scroll_step
        await page.evaluate(f"window.scrollTo(0, {current})")
        await asyncio.sleep(0.4)
        # Высота может расти по мере подгрузки — обновляем
        page_height = await page.evaluate("document.body.scrollHeight")

    # Немного назад — реалистично
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.8)")
    await asyncio.sleep(0.5)


async def check_captcha(page: Page) -> bool:
    """Возвращает True если обнаружена капча."""
    element = await page.query_selector("#captcha-container")
    return element is not None


async def get_skus_from_page(page: Page) -> list[str]:
    """
    Собирает все SKU с текущей страницы.
    Дубликаты убираем — каждая карточка ссылается дважды (фото + название).
    """
    hrefs: list[str] = await page.eval_on_selector_all(
        "a[href*='/product/']", "els => els.map(e => e.getAttribute('href'))"
    )

    seen: set[str] = set()
    skus: list[str] = []

    for href in hrefs:
        sku = extract_sku_from_href(href)
        if sku and sku not in seen:
            seen.add(sku)
            skus.append(sku)

    logger.debug(f"Со страницы собрано {len(skus)} уникальных SKU")
    return skus


async def navigate_to_search(page: Page, query: str, page_num: int) -> bool:
    """
    Переходит на страницу поиска. При первом запросе — через главную.
    Возвращает False если поймали капчу.
    """
    if page_num == 1:
        logger.info("Заходим через главную страницу...")
        await page.goto("https://www.ozon.ru/", wait_until="domcontentloaded")
        await human_delay()
        await page.mouse.move(600, 400)
        await page.evaluate("window.scrollBy(0, 300)")
        await human_delay()

    url = OZON_SEARCH_URL.format(query=query, page=page_num)
    logger.info(f"Переходим: {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await human_delay()

    if await check_captcha(page):
        logger.warning("Обнаружена капча!")
        return False

    try:
        await page.wait_for_selector("a[href*='/product/']", timeout=TIMEOUT_MS)
    except Exception:
        logger.warning(f"Карточки не появились на странице {page_num}")
        return False

    return True


async def find_sku_position(query: str, target_sku: str) -> PositionResult:
    """
    Основная функция поиска позиции SKU в выдаче Ozon.
    Проверяет до MAX_PAGES страниц (~36 товаров каждая).
    """
    position_counter = 0
    timestamp = datetime.now()

    async with create_browser_context() as (browser, context):
        page = await create_stealth_page(context)

        for page_num in range(1, MAX_PAGES + 1):
            logger.info(f"--- Страница {page_num} из {MAX_PAGES} ---")

            ok = await navigate_to_search(page, query, page_num)
            if not ok:
                logger.error(f"Не удалось загрузить страницу {page_num}")
                break

            # Скроллим чтобы подгрузить все карточки
            await scroll_page_fully(page)
            await human_delay()

            skus = await get_skus_from_page(page)

            if not skus:
                logger.warning("SKU не найдены на странице — останавливаемся")
                break

            for sku in skus:
                position_counter += 1
                logger.debug(f"  #{position_counter}: SKU {sku}")

                if sku == target_sku:
                    logger.info(
                        f"Найден! SKU {target_sku} на позиции {position_counter}"
                    )
                    return PositionResult(
                        query=query,
                        sku=target_sku,
                        position=position_counter,
                        page=page_num,
                        total_checked=position_counter,
                        timestamp=timestamp,
                    )

            logger.info(
                f"Страница {page_num}: проверено {len(skus)} товаров, "
                f"итого {position_counter}"
            )

            # Пауза между страницами
            if page_num < MAX_PAGES:
                await human_delay()

    logger.info(
        f"Товар SKU {target_sku} не найден в первых {position_counter} позициях"
    )
    return PositionResult(
        query=query,
        sku=target_sku,
        position="not_found",
        page=None,
        total_checked=position_counter,
        timestamp=timestamp,
    )