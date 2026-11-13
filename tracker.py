"""
Ядро трекера цен FazerCards:
- Отслеживание изменений цен с учетом порога MIN_DIFF
- Логика накопления мелких изменений (< MIN_DIFF)
- Автономное локальное кэширование в prices_cache.json
- Фильтрация по ключевым словам (FILTER_KEYWORDS)
- Форматирование красивых HTML-уведомлений и прайс-листов
"""

import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

from fazer_client import FazerCardsClient

logger = logging.getLogger("tracker")

# Известные базовые категории для быстрого старта (PUBG Mobile UC и авто-пополнения)
DEFAULT_KNOWN_CATEGORIES: List[Dict[str, str]] = [
    {"type": "gift_cards", "category_id": "pubg_mobile", "name": "PUBG Mobile (UC Vouchers)"},
    {"type": "topups", "category_id": "pubg_mobile_auto", "name": "PUBG Mobile (Auto Top-up)"},
]

# Ключевые слова для автоматического поиска категорий в общем каталоге (когда FILTER_KEYWORDS пуст)
POPULAR_DISCOVERY_KEYWORDS: List[str] = [
    "PUBG", "Mobile Legends", "Free Fire", "Roblox", "Steam", 
    "Genshin", "Valorant", "Call of Duty", "Brawl Stars", "PlayStation", 
    "Xbox", "Apple", "Google Play", "Discord", "Razer", "Telegram"
]


def load_cache(cache_file: str) -> Dict[str, Any]:
    """Безопасно загружает кэш цен из prices_cache.json."""
    if not os.path.exists(cache_file):
        return {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.error(f"Ошибка чтения файла кэша {cache_file}: {exc}")
        return {}


def save_cache(cache_file: str, data: Dict[str, Any]) -> None:
    """Атомарно сохраняет кэш цен во временный файл, затем заменяет основной."""
    tmp_path = f"{cache_file}.tmp_{int(time.time() * 1000)}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if os.path.exists(cache_file):
            os.replace(tmp_path, cache_file)
        else:
            os.rename(tmp_path, cache_file)
    except Exception as exc:
        logger.error(f"Ошибка сохранения кэша в {cache_file}: {exc}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def format_price_alert_html(
    product_name: str,
    old_price: float,
    new_price: float,
    diff: float,
    percent: float
) -> str:
    """
    Форматирует HTML-уведомление об изменении цены:
    - При росте: 📈 Подорожание на FazerCards!
    - При снижении: 📉 Снижение цены на FazerCards!
    Точно соответствует дизайну и стилю проекта.
    """
    if new_price > old_price:
        return (
            "📈 <b>Подорожание на FazerCards!</b>\n\n"
            f"📦 Товар: <b>{product_name}</b>\n"
            f"💵 Предыдущая цена: <code>${old_price:.2f}</code>\n"
            f"🔥 Текущая цена: <code>${new_price:.2f}</code>\n"
            f"🔺 Изменение: <b>+${diff:.2f}</b> (+{percent:.1f}%)\n\n"
            "⚠️ <i>Проверьте оптовые цены у поставщика FazerCards!</i>"
        )
    else:
        abs_diff = abs(diff)
        abs_pct = abs(percent)
        return (
            "📉 <b>Снижение цены на FazerCards!</b>\n\n"
            f"📦 Товар: <b>{product_name}</b>\n"
            f"💵 Предыдущая цена: <code>${old_price:.2f}</code>\n"
            f"✅ Текущая цена: <code>${new_price:.2f}</code>\n"
            f"🔻 Изменение: <b>-${abs_diff:.2f}</b> (-{abs_pct:.1f}%)"
        )


def format_prices_messages(offers_dict: Dict[str, Dict[str, Any]]) -> List[str]:
    """
    Группирует текущие товары по категориям и формирует красиво оформленные
    сообщения со списком актуальных цен. Если список большой, разбивает на части (<= 3500 символов).
    """
    if not offers_dict:
        return ["ℹ️ Список отслеживаемых товаров пуст или ещё не загружен."]

    # Группируем по категории
    categories: Dict[str, List[Dict[str, Any]]] = {}
    for item in offers_dict.values():
        cat_title = item.get("category_name") or item.get("category_id") or "Разное"
        if cat_title not in categories:
            categories[cat_title] = []
        categories[cat_title].append(item)

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    header = (
        "📋 <b>Актуальный прайс FazerCards</b>\n"
        f"🕒 <i>Обновлено: {now_str} (всего позиций: {len(offers_dict)})</i>\n\n"
    )

    messages: List[str] = []
    current_msg = header

    for cat_title, items in sorted(categories.items()):
        cat_block = f"🏷️ <b>{cat_title}</b>\n"
        # Сортируем товары по цене
        items_sorted = sorted(items, key=lambda x: x.get("price", 0.0))
        for it in items_sorted:
            name = it.get("offer_name") or it.get("name") or it.get("offer_id")
            price = it.get("price", 0.0)
            stock = it.get("stock")
            stock_info = f" <i>(остаток: {stock})</i>" if stock is not None else ""
            cat_block += f"  • {name} — <code>${price:.2f}</code>{stock_info}\n"
        cat_block += "\n"

        if len(current_msg) + len(cat_block) > 3500:
            messages.append(current_msg.strip())
            current_msg = cat_block
        else:
            current_msg += cat_block

    if current_msg.strip():
        messages.append(current_msg.strip())

    return messages


class PriceTracker:
    """Управляет циклом проверки цен, пороговой фильтрацией и обновлением локального кэша."""

    def __init__(
        self,
        fazer_client: FazerCardsClient,
        cache_file: str,
        min_diff: float = 0.05,
        filter_keywords: Optional[List[str]] = None,
    ):
        self.client = fazer_client
        self.cache_file = cache_file
        self.min_diff = min_diff
        self.filter_keywords = [k.strip().lower() for k in (filter_keywords or []) if k.strip()]

        # Кэш категорий в памяти
        self._cached_categories: List[Dict[str, str]] = []
        self._categories_last_updated: float = 0.0
        self.last_check_time: Optional[str] = None
        self.last_check_items_count: int = 0

    async def get_tracked_categories(self, force_refresh: bool = False) -> List[Dict[str, str]]:
        """
        Определяет список категорий для мониторинга:
        - Если заданы FILTER_KEYWORDS: ищет в FazerCards категории по каждому ключевому слову.
        - Если FILTER_KEYWORDS пуст: объединяет известные категории и популярные игры.
        Обновляет список в памяти раз в 60 минут или принудительно (force_refresh).
        """
        now = time.time()
        if self._cached_categories and not force_refresh and (now - self._categories_last_updated < 3600):
            return self._cached_categories

        logger.info("Обновление списка категорий для мониторинга...")
        categories_map: Dict[str, Dict[str, str]] = {}

        # 1. Если заданы ключевые слова фильтрации
        if self.filter_keywords:
            for kw in self.filter_keywords:
                try:
                    found = await self.client.search_catalog(kw)
                    for item in found:
                        if isinstance(item, dict):
                            cat_id = item.get("category_id")
                            item_type = item.get("type", "gift_cards")
                            name = item.get("name") or cat_id
                            if cat_id and item_type:
                                key = f"{item_type}:{cat_id}"
                                categories_map[key] = {
                                    "type": item_type,
                                    "category_id": cat_id,
                                    "name": name
                                }
                except Exception as exc:
                    logger.warning(f"Ошибка поиска в каталоге по запросу '{kw}': {exc}")

            # Если по ключевому слову "pubg" искали, убедимся что pubg_mobile_auto и pubg_mobile включены
            if any("pubg" in kw for kw in self.filter_keywords):
                for base in DEFAULT_KNOWN_CATEGORIES:
                    k = f"{base['type']}:{base['category_id']}"
                    if k not in categories_map:
                        categories_map[k] = base

        else:
            # 2. Если фильтр пуст: загружаем базовые категории + популярные товары каталога
            for base in DEFAULT_KNOWN_CATEGORIES:
                categories_map[f"{base['type']}:{base['category_id']}"] = base

            for kw in POPULAR_DISCOVERY_KEYWORDS:
                try:
                    found = await self.client.search_catalog(kw)
                    for item in found:
                        if isinstance(item, dict):
                            cat_id = item.get("category_id")
                            item_type = item.get("type", "gift_cards")
                            name = item.get("name") or cat_id
                            if cat_id and item_type:
                                key = f"{item_type}:{cat_id}"
                                categories_map[key] = {
                                    "type": item_type,
                                    "category_id": cat_id,
                                    "name": name
                                }
                except Exception as exc:
                    logger.debug(f"Ошибка автопоиска по '{kw}': {exc}")

        result = list(categories_map.values())
        logger.info(f"Найдено {len(result)} категорий для отслеживания.")
        self._cached_categories = result
        self._categories_last_updated = now
        return result

    async def fetch_current_offers(self) -> Dict[str, Dict[str, Any]]:
        """
        Запрашивает актуальные цены для всех категорий из FazerCards.
        Возвращает словарь: { "category_id:offer_id": item_data }
        """
        categories = await self.get_tracked_categories()
        results: Dict[str, Dict[str, Any]] = {}
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for cat in categories:
            item_type = cat["type"]
            cat_id = cat["category_id"]
            cat_name = cat.get("name", cat_id)

            try:
                res = await self.client.get_offers(item_type=item_type, category_id=cat_id)
            except Exception as exc:
                logger.error(f"Ошибка запроса get_offers({item_type}, {cat_id}): {exc}")
                continue

            if not res or not isinstance(res, dict) or res.get("isError"):
                continue

            offers = res.get("offers", [])
            if not isinstance(offers, list):
                continue

            for o in offers:
                if not isinstance(o, dict):
                    continue

                offer_id = str(o.get("offer_id") or o.get("card_id") or o.get("key_id") or "").strip()
                if not offer_id:
                    continue

                offer_name = str(o.get("name") or o.get("title") or offer_id).strip()
                raw_price = o.get("price_usd") or o.get("price") or 0.0

                try:
                    price_val = round(float(raw_price), 4)
                except (ValueError, TypeError):
                    continue

                # Формируем читаемое полное имя товара
                if offer_name.lower().startswith(cat_name.lower()):
                    full_name = offer_name
                else:
                    full_name = f"{cat_name} — {offer_name}"

                # Если заданы FILTER_KEYWORDS — проверяем соответствие
                if self.filter_keywords:
                    search_corpus = f"{cat_name} {cat_id} {offer_name} {full_name}".lower()
                    if not any(kw in search_corpus for kw in self.filter_keywords):
                        continue

                item_key = f"{cat_id}:{offer_id}"
                stock_val = o.get("stock")

                results[item_key] = {
                    "key": item_key,
                    "category_id": cat_id,
                    "offer_id": offer_id,
                    "type": item_type,
                    "category_name": cat_name,
                    "offer_name": offer_name,
                    "full_name": full_name,
                    "price": price_val,
                    "stock": stock_val,
                    "updated_at": now_str,
                }

        self.last_check_time = now_str
        self.last_check_items_count = len(results)
        return results

    async def check_prices(self, force_all: bool = False) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """
        Выполняет один цикл проверки цен:
        1. Запрашивает текущие офферы из FazerCards.
        2. Загружает базовые цены из prices_cache.json.
        3. Если кэш пуст (первый запуск): инициализирует базу без рассылки спама.
        4. Находит изменения цены >= MIN_DIFF.
        5. ВНИМАНИЕ: Базовая цена в кэше обновляется ТОЛЬКО после фактической
           успешной отправки уведомления (через mark_item_notified).
           Мелкие колебания (< MIN_DIFF) сохраняются в кэше как старая база, накапливаясь.

        Возвращает:
            (is_first_run, detected_changes, current_offers)
        """
        current_offers = await self.fetch_current_offers()
        if not current_offers:
            logger.warning("Не удалось получить ни одного товара от FazerCards.")
            return False, [], {}

        cache = load_cache(self.cache_file)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Проверяем, первый ли это запуск (кэш пуст)
        is_first_run = (len(cache) == 0)
        if is_first_run and not force_all:
            save_cache(self.cache_file, current_offers)
            logger.info(
                f"Кэш FazerCards впервые инициализирован: сохранено {len(current_offers)} товаров. "
                "Уведомления при первом старте не отправляются."
            )
            return True, [], current_offers

        detected_changes: List[Dict[str, Any]] = []

        for item_key, item_data in current_offers.items():
            new_price = float(item_data.get("price", 0.0))
            cached_entry = cache.get(item_key)

            if not cached_entry:
                # Новый товар появился в каталоге: добавляем в кэш как новую базовую цену
                cache[item_key] = item_data
                if force_all:
                    old_price = round(max(0.01, new_price - 0.50), 2)
                else:
                    continue
            else:
                old_price = float(cached_entry.get("price", 0.0))

            diff = new_price - old_price

            # Проверка порога MIN_DIFF
            if abs(diff) >= self.min_diff or force_all:
                percent = (diff / old_price * 100.0) if old_price > 0 else 0.0
                prod_name = item_data.get("full_name") or cached_entry.get("full_name") or item_key

                detected_changes.append({
                    "item_key": item_key,
                    "product_name": prod_name,
                    "old_price": old_price,
                    "new_price": new_price,
                    "diff": diff,
                    "percent": percent,
                    "item_data": item_data,
                    "html": format_price_alert_html(
                        product_name=prod_name,
                        old_price=old_price,
                        new_price=new_price,
                        diff=diff,
                        percent=percent
                    )
                })
            else:
                # Колебание меньше MIN_DIFF: НЕ трогаем cached_entry['price'],
                # чтобы постепенные изменения накапливались!
                logger.debug(
                    f"Товар '{item_data.get('full_name')}': разница ${diff:+.4f} < ${self.min_diff:.2f}. "
                    f"Базовая цена остаётся ${old_price:.4f}."
                )

        # Сохраняем обновленные записи (новые товары и актуальные метаданные)
        save_cache(self.cache_file, cache)
        return False, detected_changes, current_offers

    def mark_item_notified(self, item_key: str, item_data: Dict[str, Any]) -> None:
        """
        Обновляет базовую цену товара в кэше ТОЛЬКО после подтверждения
        успешной отправки уведомления в Telegram.
        """
        cache = load_cache(self.cache_file)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        copy_data = dict(item_data)
        copy_data["last_notified_at"] = now_str
        cache[item_key] = copy_data

        save_cache(self.cache_file, cache)
        logger.info(f"Базовая цена в кэше обновлена для '{item_data.get('full_name')}' -> ${item_data.get('price'):.4f}")
