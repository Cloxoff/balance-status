"""
FazerCards Price Tracker — Главный запускаемый файл.
Автономный Telegram-бот и фоновый мониторинг оптовых цен FazerCards в реальном времени.

Запуск:
    python main.py                  # Стандартный запуск (бот + мониторинг)
    python main.py --check-now      # Однократная проверка цен без ожидания
    python main.py --test-alert     # Отправка тестового уведомления в Telegram
    python main.py --reset-cache    # Сброс локального файла кэша цен
"""

import os
import sys
import asyncio
import logging
from typing import List

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from fazer_client import FazerCardsClient
from tracker import PriceTracker, format_price_alert_html, format_prices_messages

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")


def check_auth(user_id: int) -> bool:
    """Проверяет, авторизован ли пользователь Telegram по списку CHAT_IDS."""
    if not config.CHAT_IDS:
        return True
    return user_id in config.CHAT_IDS


async def run_bot_and_monitor():
    """Основная точка входа: инициализация и параллельный запуск бота и монитора."""
    errors = config.validate_config()
    if errors:
        print("\n" + "=" * 65)
        print("  ❌ ОШИБКА КОНФИГУРАЦИИ (.env)")
        print("=" * 65)
        for err in errors:
            print(f"  • {err}")
        print("\nПожалуйста, создайте файл .env на основе .env.example и укажите ваши ключи.")
        print("=" * 65 + "\n")
        sys.exit(1)

    # Инициализация FazerCards клиента и трекера
    client = FazerCardsClient(
        endpoint=config.FAZERCARDS_API_URL,
        api_key=config.FAZERCARDS_API_KEY
    )
    tracker = PriceTracker(
        fazer_client=client,
        cache_file=config.CACHE_FILE,
        min_diff=config.MIN_DIFF,
        filter_keywords=config.FILTER_KEYWORDS
    )

    # Инициализация Telegram-бота
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # ─── Telegram Bot Handlers ──────────────────────────────────────────────

    @dp.message(Command("start", "help"))
    async def cmd_start(message: types.Message):
        if not check_auth(message.from_user.id):
            await message.answer("⛔ <b>Доступ ограничен.</b>\nЭтот бот настроен для персонального использования.")
            return

        filter_desc = ", ".join(config.FILTER_KEYWORDS) if config.FILTER_KEYWORDS else "Все доступные игры"
        text = (
            "👋 <b>FazerCards Price Tracker Bot</b>\n\n"
            "Автономный бот для круглосуточного отслеживания оптовых цен FazerCards.\n\n"
            "<b>Доступные команды:</b>\n"
            "📊 /prices — актуальный прайс-лист отслеживаемых товаров\n"
            "🔍 /check — принудительно проверить цены прямо сейчас\n"
            "⚙️ /status — текущие настройки трекера и статистика\n"
            "❓ /help — справка по командам\n\n"
            f"<i>Текущий фильтр: {filter_desc} | Порог: ${config.MIN_DIFF:.2f}</i>"
        )
        await message.answer(text)

    @dp.message(Command("status"))
    async def cmd_status(message: types.Message):
        if not check_auth(message.from_user.id):
            await message.answer("⛔ Доступ ограничен.")
            return

        filter_text = ", ".join(config.FILTER_KEYWORDS) if config.FILTER_KEYWORDS else "Все доступные игры (без фильтра)"
        status_text = (
            "⚙️ <b>Параметры FazerCards Tracker:</b>\n\n"
            f"⏱ <b>Интервал проверки:</b> <code>{config.CHECK_INTERVAL} сек</code>\n"
            f"📏 <b>Порог уведомления:</b> <code>${config.MIN_DIFF:.2f}</code>\n"
            f"🎯 <b>Фильтр товаров:</b> <code>{filter_text}</code>\n"
            f"📦 <b>Позиций отслеживается:</b> <code>{tracker.last_check_items_count}</code>\n"
            f"🕒 <b>Последняя проверка:</b> <code>{tracker.last_check_time or 'Ещё не запускалась'}</code>\n"
            f"💾 <b>Файл кэша:</b> <code>prices_cache.json</code>\n"
        )
        await message.answer(status_text)

    @dp.message(Command("prices"))
    async def cmd_prices(message: types.Message):
        if not check_auth(message.from_user.id):
            await message.answer("⛔ Доступ ограничен.")
            return

        wait_msg = await message.answer("⏳ <i>Запрашиваю актуальные цены FazerCards...</i>")
        try:
            offers = await tracker.fetch_current_offers()
            messages = format_prices_messages(offers)
            await wait_msg.delete()
            for msg_chunk in messages:
                await message.answer(msg_chunk)
        except Exception as exc:
            logger.error(f"Ошибка выполнения /prices: {exc}")
            await wait_msg.edit_text(f"❌ <b>Ошибка получения цен:</b> {exc}")

    @dp.message(Command("check"))
    async def cmd_check(message: types.Message):
        if not check_auth(message.from_user.id):
            await message.answer("⛔ Доступ ограничен.")
            return

        wait_msg = await message.answer("🔍 <i>Выполняю проверку цен на FazerCards...</i>")
        try:
            is_first_run, changes, current_offers = await tracker.check_prices()
            await wait_msg.delete()

            if is_first_run:
                await message.answer(
                    f"✅ <b>Кэш цен успешно инициализирован!</b>\n"
                    f"Сохранено товаров: <b>{len(current_offers)}</b>.\n"
                    "Со следующей проверки вы будете получать уведомления при изменениях цен."
                )
                return

            if changes:
                # Отправляем уведомления об изменениях
                for chg in changes:
                    await message.answer(chg["html"])
                    tracker.mark_item_notified(chg["item_key"], chg["item_data"])

                await message.answer(f"✅ <b>Проверка завершена!</b> Обнаружено изменений: <b>{len(changes)}</b>.")
            else:
                await message.answer(
                    f"✅ <b>Проверка завершена.</b>\n"
                    f"Изменений цен выше порога (<code>${config.MIN_DIFF:.2f}</code>) не обнаружено.\n"
                    f"Всего проверено товаров: <b>{len(current_offers)}</b>."
                )
        except Exception as exc:
            logger.error(f"Ошибка выполнения /check: {exc}")
            await wait_msg.edit_text(f"❌ <b>Ошибка проверки цен:</b> {exc}")

    # ─── Background Monitor Task ────────────────────────────────────────────

    async def monitor_loop():
        logger.info(
            f"Фоновый мониторинг запущен (интервал: {config.CHECK_INTERVAL}с, "
            f"порог: >= ${config.MIN_DIFF:.2f})."
        )
        await asyncio.sleep(3)

        while True:
            try:
                is_first_run, changes, current_offers = await tracker.check_prices()

                if is_first_run:
                    # Уведомляем о старте трекера
                    for cid in config.CHAT_IDS:
                        try:
                            await bot.send_message(
                                cid,
                                f"🚀 <b>FazerCards Price Tracker успешно запущен!</b>\n\n"
                                f"📦 Начальная база сохранена: <b>{len(current_offers)}</b> позиций.\n"
                                f"⏱ Проверка каждые: <code>{config.CHECK_INTERVAL} сек</code>\n"
                                f"📏 Порог оповещения: <code>${config.MIN_DIFF:.2f}</code>\n\n"
                                "<i>Бот пришлёт уведомление, как только цена изменится!</i>"
                            )
                        except Exception as ex:
                            logger.warning(f"Не удалось отправить уведомление о запуске в чат {cid}: {ex}")

                elif changes:
                    logger.info(f"Обнаружено {len(changes)} изменений цен. Отправка оповещений...")
                    for chg in changes:
                        item_key = chg["item_key"]
                        item_data = chg["item_data"]
                        html_text = chg["html"]
                        any_sent = False

                        for cid in config.CHAT_IDS:
                            try:
                                await bot.send_message(cid, html_text)
                                any_sent = True
                            except Exception as ex:
                                logger.error(f"Ошибка отправки оповещения в чат {cid}: {ex}")

                        # Базовая цена обновляется ТОЛЬКО при успешной доставке уведомления!
                        if any_sent:
                            tracker.mark_item_notified(item_key, item_data)

            except asyncio.CancelledError:
                logger.info("Фоновый мониторинг остановлен.")
                break
            except Exception as exc:
                logger.error(f"Непредвиденная ошибка в цикле мониторинга: {exc}", exc_info=True)

            try:
                await asyncio.sleep(config.CHECK_INTERVAL)
            except asyncio.CancelledError:
                break

    # Запускаем параллельно polling бота и фоновый мониторинг
    monitor_task = asyncio.create_task(monitor_loop())

    try:
        print("\n" + "=" * 65)
        print("  🚀 FazerCards Tracker успешно запущен и работает!")
        print(f"  • Получатели (CHAT_ID): {config.CHAT_IDS}")
        print(f"  • Интервал:             {config.CHECK_INTERVAL} сек")
        print(f"  • Порог уведомления:    >= ${config.MIN_DIFF:.2f}")
        print(f"  • Фильтр товаров:       {', '.join(config.FILTER_KEYWORDS) if config.FILTER_KEYWORDS else 'Все товары'}")
        print("=" * 65 + "\n")
        await dp.start_polling(bot)
    finally:
        monitor_task.cancel()
        await bot.session.close()


# ─── CLI Utilities ──────────────────────────────────────────────────────────

async def run_cli_test_alert():
    """Отправляет тестовое уведомление в Telegram для проверки подключения."""
    errors = config.validate_config()
    if not config.BOT_TOKEN or not config.CHAT_IDS:
        print("❌ Ошибка: Укажите BOT_TOKEN и CHAT_ID в файле .env")
        return

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    test_html = format_price_alert_html(
        product_name="PUBG Mobile 60 UC (Тестовое уведомление)",
        old_price=0.88,
        new_price=0.95,
        diff=0.07,
        percent=8.0
    )
    print(f"Отправка тестового оповещения в чаты {config.CHAT_IDS}...")
    success = False
    for cid in config.CHAT_IDS:
        try:
            await bot.send_message(cid, test_html)
            print(f"  ✅ Успешно отправлено в chat_id={cid}")
            success = True
        except Exception as exc:
            print(f"  ❌ Ошибка отправки в chat_id={cid}: {exc}")
    await bot.session.close()
    if success:
        print("🎉 Тест завершён успешно!")


async def run_cli_check_now():
    """Выполняет однократную проверку цен и выводит результат в консоль."""
    errors = config.validate_config()
    if errors:
        for e in errors:
            print(f"  • {e}")
        return

    client = FazerCardsClient(endpoint=config.FAZERCARDS_API_URL, api_key=config.FAZERCARDS_API_KEY)
    tracker = PriceTracker(
        fazer_client=client,
        cache_file=config.CACHE_FILE,
        min_diff=config.MIN_DIFF,
        filter_keywords=config.FILTER_KEYWORDS
    )
    print("Запрос товаров из FazerCards...")
    is_first_run, changes, current_offers = await tracker.check_prices()
    print(f"Получено товаров: {len(current_offers)}")
    print(f"Первый запуск: {'Да (кэш создан)' if is_first_run else 'Нет'}")
    print(f"Обнаружено изменений цен >= ${config.MIN_DIFF:.2f}: {len(changes)}")
    for chg in changes:
        print(f"  • {chg['product_name']}: ${chg['old_price']:.2f} -> ${chg['new_price']:.2f} (diff={chg['diff']:+.2f})")


def main():
    if "--test-alert" in sys.argv:
        asyncio.run(run_cli_test_alert())
        return

    if "--check-now" in sys.argv:
        asyncio.run(run_cli_check_now())
        return

    if "--reset-cache" in sys.argv:
        if os.path.exists(config.CACHE_FILE):
            os.remove(config.CACHE_FILE)
            print(f"Файл кэша {config.CACHE_FILE} удалён.")
        else:
            print("Файл кэша ещё не был создан.")
        return

    try:
        asyncio.run(run_bot_and_monitor())
    except (KeyboardInterrupt, SystemExit):
        print("\nТрекер остановлен пользователем.")


if __name__ == "__main__":
    main()
