"""
Точка входа CLI. Запускает парсер и выводит результат в JSON.

Использование:
    python script.py --query "нож туристический" --sku 885084802
    python script.py --query "термос" --sku 123456789 --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ozon.parser import find_sku_position


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # Убираем лишний шум от playwright
    logging.getLogger("playwright").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Поиск позиции товара в выдаче Ozon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python script.py --query "нож туристический" --sku 885084802
  python script.py --query "термос" --sku 123456789 --verbose
        """,
    )
    parser.add_argument(
        "--query",
        required=True,
        type=str,
        help="Поисковый запрос (например: 'нож туристический')",
    )
    parser.add_argument(
        "--sku",
        required=True,
        type=str,
        help="Артикул товара на Ozon",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Подробный вывод логов (для отладки)",
    )
    return parser.parse_args()


async def run(query: str, sku: str) -> None:
    logging.info(f"Ищем SKU {sku} по запросу '{query}'...")

    result = await find_sku_position(query=query, target_sku=sku)

    # JSON-вывод строго по формату задания
    print(result.to_json())


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    try:
        asyncio.run(run(args.query, args.sku))
    except KeyboardInterrupt:
        logging.info("Прервано пользователем")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
