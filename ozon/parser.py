from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from playwright.async_api import Page

from config import (
    CAPTCHA_CHECK_INTERVAL,
    CAPTCHA_WAIT_SECONDS,
    MAX_EXECUTION_SECONDS,
    MAX_PAGES,
    MAX_RETRIES,
    NAVIGATION_WAIT,
    PRODUCT_SELECTOR_TIMEOUT_MS,
    RETRY_DELAY_MAX,
    RETRY_DELAY_MIN,
    SCROLL_MAX_ITERATIONS,
    SCROLL_STABILIZE_THRESHOLD,
    SCROLL_WAIT_TIME,
    TIMEOUT_MS,
    WAIT_SELECTOR_TIMEOUT_MS,
)
from ozon.browser import create_browser_context, create_stealth_page, human_delay
from ozon.models import PositionResult

logger = logging.getLogger(__name__)

# SKU — число в конце пути URL перед слешем или параметрами
SKU_PATTERN = re.compile(r"-(\d{5,12})(?:/|\?|$)")

OZON_SEARCH_URL = (
    "https://www.ozon.ru/search/?text={query}&from_global=true&page={page}"
)

# Селекторы карточек товаров (основной + fallback)
PRODUCT_LINK_SELECTORS: list[str] = [
    "a[href*='/product/']",
    "div[data-test='productCard'] a[href]",
    "div[class*='product'] a[href*='product']",
]

PRODUCT_CARD_SELECTORS: list[str] = [
    "a[href*='/product/']",
    "div[data-test='productCard']",
    "[data-widget*='searchResults']",
    "[data-index]",
]

BLOCK_SIGNALS: tuple[str, ...] = (
    "доступ ограничен",
    "access denied",
    "не робот",
    "подтвердите, что вы не робот",
    "antibot",
    "похоже, нет соединения",
    "похоже, нет\u00a0соединения",
    "выключите vpn",
    "fab_chlg_",
)


def extract_sku_from_href(href: str) -> Optional[str]:
    """Извлекает SKU из ссылки на товар Ozon."""
    match = SKU_PATTERN.search(href)
    return match.group(1) if match else None


async def count_unique_skus_on_page(page: Page) -> int:
    """Считает уникальные SKU на странице (без дубликатов ссылок)."""
    hrefs: list[str] = []
    for selector in PRODUCT_LINK_SELECTORS:
        try:
            hrefs = await page.eval_on_selector_all(
                selector,
                "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
            )
            if hrefs:
                break
        except Exception:
            continue

    seen: set[str] = set()
    for href in hrefs:
        sku = extract_sku_from_href(href)
        if sku:
            seen.add(sku)
    return len(seen)


async def _trigger_lazy_load(page: Page) -> None:
    """Прокручивает страницу так, чтобы Ozon подгрузил следующую порцию карточек."""
    await page.evaluate(
        """() => {
        const links = [...document.querySelectorAll("a[href*='/product/']")];
        if (links.length > 0) {
            links[links.length - 1].scrollIntoView({ behavior: 'instant', block: 'end' });
        }
        window.scrollBy(0, window.innerHeight * 0.9);
        window.scrollTo(0, document.body.scrollHeight);
    }"""
    )


async def scroll_page_fully(page: Page) -> None:
    """
    Прокручивает страницу вниз с ожиданием стабилизации количества товаров.

    Lazy loading на Ozon подгружает карточки при скролле. Функция:
    - скроллит к последней карточке и вниз страницы
    - ждёт SCROLL_WAIT_TIME сек
    - считает уникальные SKU (не ссылки — у карточки их две)
    - если количество выросло — продолжает скролл
    - если 3 итерации подряд не изменилось — считает, что всё подгружено
    - максимум SCROLL_MAX_ITERATIONS чтобы не зависнуть
    """
    stable_count = 0
    previous_sku_count = 0
    iteration = 0

    while iteration < SCROLL_MAX_ITERATIONS:
        iteration += 1

        await _trigger_lazy_load(page)
        await asyncio.sleep(SCROLL_WAIT_TIME)

        current_sku_count = await count_unique_skus_on_page(page)

        if current_sku_count == previous_sku_count:
            stable_count += 1
            logger.debug(
                f"Скролл итерация {iteration}: {current_sku_count} уникальных SKU "
                f"(стабилен {stable_count}/{SCROLL_STABILIZE_THRESHOLD})"
            )
            if stable_count >= SCROLL_STABILIZE_THRESHOLD:
                logger.debug(
                    f"Lazy loading завершён: подгружено {current_sku_count} SKU "
                    f"(стабильно {stable_count} итераций)"
                )
                break
        else:
            logger.debug(
                f"Скролл итерация {iteration}: SKU "
                f"{previous_sku_count} → {current_sku_count}"
            )
            stable_count = 0
            previous_sku_count = current_sku_count

    await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.75)")
    await asyncio.sleep(0.3)


async def check_captcha(page: Page) -> bool:
    """Возвращает True если обнаружена капча."""
    selectors = ("#captcha-container", "iframe[src*='captcha']", "[class*='captcha']")
    for selector in selectors:
        element = await page.query_selector(selector)
        if element is not None:
            return True
    return False


async def check_access_blocked(page: Page) -> bool:
    """Проверяет страницу блокировки IP / antibot без карточек товаров."""
    try:
        page_text = await page.evaluate(
            "() => (document.body?.innerText || '').slice(0, 1500).toLowerCase()"
        )
    except Exception:
        return False

    return any(signal in page_text for signal in BLOCK_SIGNALS)


async def wait_for_captcha_resolution(page: Page, context_label: str) -> bool:
    """
    Ждёт решения капчи короткими интервалами.
    Можно прервать Ctrl+C; капча проверяется каждые CAPTCHA_CHECK_INTERVAL сек.
    """
    logger.warning(
        f"{context_label}: обнаружена капча — ждём до {CAPTCHA_WAIT_SECONDS:.0f} сек "
        f"(проверка каждые {CAPTCHA_CHECK_INTERVAL:.0f} сек, Ctrl+C для выхода)"
    )

    waited = 0.0
    while waited < CAPTCHA_WAIT_SECONDS:
        if not await check_captcha(page):
            logger.info(f"{context_label}: капча решена за {waited:.0f} сек")
            return True

        chunk = min(CAPTCHA_CHECK_INTERVAL, CAPTCHA_WAIT_SECONDS - waited)
        await asyncio.sleep(chunk)
        waited += chunk

    if await check_captcha(page):
        logger.error(
            f"{context_label}: капча не решена за {CAPTCHA_WAIT_SECONDS:.0f} сек"
        )
        return False

    logger.info(f"{context_label}: капча решена")
    return True


async def wait_for_products(page: Page) -> bool:
    """
    Быстро проверяет наличие товаров:
    1) мгновенный query_selector
    2) ожидание виджета выдачи или карточек
    """
    for selector in PRODUCT_CARD_SELECTORS:
        if await page.query_selector(selector):
            logger.debug(f"Товары найдены сразу (селектор: {selector})")
            return True

    # Даём Ozon время подгрузить виджет searchResults
    try:
        await page.wait_for_selector(
            "[data-widget*='searchResults'], [data-index], a[href*='/product/']",
            timeout=max(PRODUCT_SELECTOR_TIMEOUT_MS, 15000),
        )
        logger.debug("Виджет выдачи или карточки появились")
        return True
    except Exception:
        pass

    for selector in PRODUCT_CARD_SELECTORS:
        try:
            await page.wait_for_selector(
                selector,
                timeout=PRODUCT_SELECTOR_TIMEOUT_MS,
            )
            logger.debug(f"Товары появились (селектор: {selector})")
            return True
        except Exception:
            logger.debug(
                f"Селектор не найден за {PRODUCT_SELECTOR_TIMEOUT_MS}ms: {selector}"
            )

    return False


async def ensure_search_results(page: Page, page_num: int) -> bool:
    """
    Убеждается, что выдача загрузилась.
    Если карточек подозрительно мало — одна перезагрузка страницы.
    """
    if not await wait_for_products(page):
        return False

    sku_count = await count_unique_skus_on_page(page)
    if sku_count >= 12:
        return True

    logger.warning(
        f"Страница {page_num}: мало товаров ({sku_count}) — "
        f"пробуем перезагрузить выдачу"
    )
    try:
        await page.reload(wait_until=NAVIGATION_WAIT, timeout=TIMEOUT_MS)
        await asyncio.sleep(2)
    except Exception as e:
        logger.debug(f"Перезагрузка страницы {page_num}: {e}")
        return sku_count > 0

    if await check_captcha(page):
        if not await wait_for_captcha_resolution(page, f"Страница {page_num} (reload)"):
            return False

    if not await wait_for_products(page):
        return sku_count > 0

    reloaded_count = await count_unique_skus_on_page(page)
    logger.info(
        f"Страница {page_num}: после перезагрузки товаров {sku_count} → {reloaded_count}"
    )
    return reloaded_count > 0


async def get_skus_from_page(page: Page) -> list[str]:
    """
    Собирает все SKU с текущей страницы.
    Дубликаты убираем — каждая карточка ссылается дважды (фото + название).
    """
    hrefs: list[str] = []

    for selector in PRODUCT_LINK_SELECTORS:
        try:
            hrefs = await page.eval_on_selector_all(
                selector,
                "els => els.map(e => e.getAttribute('href')).filter(h => h && h.includes('product'))",
            )
            if hrefs:
                logger.debug(f"Элементы найдены по селектору: {selector}")
                break
        except Exception as e:
            logger.debug(f"Селектор не сработал ({selector}): {e}")

    if not hrefs:
        logger.warning("Ни один селектор не вернул результаты")
        return []

    seen: set[str] = set()
    skus: list[str] = []

    for href in hrefs:
        if not href:
            continue
        sku = extract_sku_from_href(href)
        if sku and sku not in seen:
            seen.add(sku)
            skus.append(sku)

    logger.debug(f"Со страницы собрано {len(skus)} уникальных SKU")
    return skus


async def _main_page_ready(page: Page) -> bool:
    """Проверяет, что главная Ozon загружена и это не страница блокировки."""
    if await check_access_blocked(page):
        return False

    selectors = (
        "[data-widget='searchBarDesktop'] input",
        "input[placeholder*='Найти']",
        "input[type='search']",
        "header",
        "form[action*='search']",
    )
    for selector in selectors:
        if await page.query_selector(selector):
            return True

    try:
        await page.wait_for_selector(
            ", ".join(selectors),
            timeout=WAIT_SELECTOR_TIMEOUT_MS,
        )
        return True
    except Exception:
        return not await check_access_blocked(page)


async def navigate_to_search(page: Page, query: str, page_num: int) -> bool:
    """
    Переходит на страницу поиска. При первом запросе — через главную.
    Возвращает False если обнаружена блокировка, капча или нет товаров.
    """
    if page_num == 1:
        logger.info("Заходим через главную страницу...")
        try:
            await page.goto(
                "https://www.ozon.ru/",
                wait_until=NAVIGATION_WAIT,
                timeout=TIMEOUT_MS,
            )
        except Exception as e:
            logger.warning(f"Главная не загрузилась полностью: {e}")

        if await check_captcha(page):
            if not await wait_for_captcha_resolution(page, "Главная"):
                return False
            # После капчи перезагружаем главную — DOM часто в неконсистентном состоянии
            try:
                await page.goto(
                    "https://www.ozon.ru/",
                    wait_until=NAVIGATION_WAIT,
                    timeout=TIMEOUT_MS,
                )
            except Exception as e:
                logger.warning(f"Перезагрузка главной после капчи: {e}")

        if not await _main_page_ready(page):
            logger.error("Главная: страница недоступна (блокировка или нет элементов)")
            return False

        logger.debug("Главная страница загрузилась")

        try:
            await page.mouse.move(600, 400)
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollBy(0, 300)")
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollBy(0, -100)")
        except Exception as e:
            logger.debug(f"Скролл на главной не критичен: {e}")

        await human_delay()

        logger.info(f"Вводим запрос в поиск: '{query}'")
        search_input = page.locator(
            "[data-widget='searchBarDesktop'] input, "
            "input[placeholder*='Найти'], "
            "input[type='search'], "
            "input[name='text']"
        ).first
        try:
            await search_input.wait_for(state="visible", timeout=10000)
            await search_input.click(timeout=5000)
            await search_input.fill("")
            await search_input.fill(query)
            await human_delay()
            await search_input.press("Enter")
            await page.wait_for_load_state(NAVIGATION_WAIT, timeout=TIMEOUT_MS)
        except Exception as e:
            logger.warning(f"Поиск через форму не сработал ({e}) — переходим по URL")
            url = OZON_SEARCH_URL.format(query=quote(query), page=page_num)
            try:
                await page.goto(url, wait_until=NAVIGATION_WAIT, timeout=TIMEOUT_MS)
            except Exception as goto_error:
                logger.warning(f"Переход на страницу поиска не полный: {goto_error}")
    else:
        url = OZON_SEARCH_URL.format(query=quote(query), page=page_num)
        logger.info(f"Переходим на поиск: {url}")
        try:
            await page.goto(url, wait_until=NAVIGATION_WAIT, timeout=TIMEOUT_MS)
        except Exception as e:
            logger.warning(f"Переход на страницу поиска не полный: {e}")

    await human_delay()

    if await check_captcha(page):
        if not await wait_for_captcha_resolution(page, f"Страница {page_num}"):
            return False

    if await check_access_blocked(page):
        logger.error(f"Страница {page_num}: доступ ограничён (возможна блокировка IP)")
        return False

    if not await ensure_search_results(page, page_num):
        logger.warning(
            f"На странице {page_num} не найдены товары — "
            f"возможно блокировка или нет результатов"
        )
        return False

    return True


def _execution_time_exceeded(started_at: float) -> bool:
    """Проверяет превышение лимита времени выполнения."""
    if MAX_EXECUTION_SECONDS <= 0:
        return False
    return (time.monotonic() - started_at) >= MAX_EXECUTION_SECONDS


async def find_sku_position(query: str, target_sku: str) -> PositionResult:
    """
    Основная функция поиска позиции SKU в выдаче Ozon.
    Проверяет до MAX_PAGES страниц (~36 товаров каждая).

    При блокировке IP (если navigate_to_search вернёт False):
    - ждёт случайное время (RETRY_DELAY_MIN — RETRY_DELAY_MAX)
    - создаёт новый браузерный контекст и пробует снова
    - максимум MAX_RETRIES раз на страницу
    """
    position_counter = 0
    timestamp = datetime.now()
    started_at = time.monotonic()

    for page_num in range(1, MAX_PAGES + 1):
        if _execution_time_exceeded(started_at):
            logger.error("Превышен лимит MAX_EXECUTION_SECONDS — останавливаемся")
            break

        logger.info(f"--- Страница {page_num} из {MAX_PAGES} ---")

        page_loaded = False
        skus: list[str] = []

        for retry_attempt in range(MAX_RETRIES + 1):
            if _execution_time_exceeded(started_at):
                logger.error("Превышен лимит MAX_EXECUTION_SECONDS — останавливаемся")
                break

            async with create_browser_context() as (_, context):
                page = await create_stealth_page(context)
                ok = await navigate_to_search(page, query, page_num)

                if ok:
                    await scroll_page_fully(page)
                    await human_delay()
                    skus = await get_skus_from_page(page)
                    if skus:
                        page_loaded = True
                        break

                    logger.warning(
                        f"Страница {page_num}: товары на странице есть, "
                        f"но SKU не извлечены"
                    )
                    ok = False

            if ok:
                break

            if retry_attempt < MAX_RETRIES:
                wait_time = random.uniform(RETRY_DELAY_MIN, RETRY_DELAY_MAX)
                logger.warning(
                    f"Попытка {retry_attempt + 1}/{MAX_RETRIES + 1}: "
                    f"страница {page_num} недоступна — ждём {wait_time:.0f} сек "
                    f"и перезапускаем браузер..."
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    f"Страница {page_num} недоступна после {MAX_RETRIES + 1} попыток"
                )

        if not page_loaded:
            logger.error(
                f"Не удалось загрузить страницу {page_num} — "
                f"дальнейший поиск даст неверные позиции, останавливаемся"
            )
            break

        for sku in skus:
            position_counter += 1
            logger.debug(f"  #{position_counter}: SKU {sku}")

            if sku == target_sku:
                logger.info(f"Найден! SKU {target_sku} на позиции {position_counter}")
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
