"""
Диагностический скрипт — сохраняет HTML выдачи Ozon для анализа структуры.
Не входит в финальный продукт, используется для разработки парсера.
"""

import asyncio
from pathlib import Path

from playwright.async_api import Page
from ozon.browser import create_browser_context, create_stealth_page, human_delay

QUERY = "нож туристический"
OUTPUT_FILE = "debug_page.html"


async def main() -> None:
    print(f"[*] Открываем Ozon с запросом: '{QUERY}'")

    async with create_browser_context() as (browser, context):
        page: Page = await create_stealth_page(context)

        # Сначала главная страница — как живой пользователь
        print("[*] Заходим на главную страницу...")
        await page.goto("https://www.ozon.ru/", wait_until="domcontentloaded")
        await human_delay()

        # Имитируем чтение страницы — скроллим вниз и вверх
        print("[*] Имитируем поведение пользователя...")
        await page.mouse.move(600, 400)
        await page.evaluate("window.scrollBy(0, 300)")
        await human_delay()
        await page.evaluate("window.scrollBy(0, -100)")
        await human_delay()

        # Теперь идём на поиск
        url = f"https://www.ozon.ru/search/?text={QUERY}&from_global=true"
        print(f"[*] Переходим к поиску: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await human_delay()

        # Проверяем капчу
        is_captcha = await page.query_selector("#captcha-container")
        if is_captcha:
            print("[-] Обнаружена капча!")
            if not HEADLESS:
                print("[*] Браузер видимый — жди решения капчи вручную (30 сек)...")
                await asyncio.sleep(30)
            else:
                print("[-] Headless режим — капча неразрешима автоматически")
                return

        # Ждём карточки
        try:
            await page.wait_for_selector("a[href*='/product/']", timeout=15000)
            print("[+] Карточки найдены")
        except Exception:
            print("[-] Карточки не появились")

        html = await page.content()
        Path(OUTPUT_FILE).write_text(html, encoding="utf-8")
        print(f"[+] HTML сохранён ({len(html)} символов)")

        links = await page.eval_on_selector_all(
            "a[href*='/product/']", "els => els.map(e => e.getAttribute('href'))"
        )
        print(f"\n[*] Найдено ссылок: {len(links)}")
        for link in links[:10]:
            print(f"    {link}")


if __name__ == "__main__":
    asyncio.run(main())
