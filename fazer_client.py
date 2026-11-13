"""
Автономный асинхронный клиент для FazerCards Model Context Protocol (MCP) JSON-RPC 2.0 API.
Не зависит от баз данных магазина и сторонних библиотек проекта.
"""

import json
import logging
from typing import Optional, Dict, Any, List
import aiohttp

logger = logging.getLogger("fazer_client")


class FazerCardsClient:
    """Клиент для работы с API поставщика FazerCards через JSON-RPC 2.0 protocol."""

    def __init__(self, endpoint: str, api_key: str):
        self.endpoint = endpoint.strip()
        self.api_key = api_key.strip()

    async def _call_tool(self, name: str, arguments: Optional[dict] = None) -> Optional[Any]:
        """
        Вызывает инструмент (tool) на MCP-сервере FazerCards по стандарту JSON-RPC 2.0.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {}
            }
        }
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "error" in data:
                            logger.error(f"FazerCards MCP JSON-RPC ошибка: {data['error']}")
                            return {"error": data["error"], "isError": True}

                        result = data.get("result", {})
                        if result.get("isError"):
                            content = result.get("content", [])
                            err_msg = "Неизвестная ошибка FazerCards"
                            if content and content[0].get("type") == "text":
                                try:
                                    err_data = json.loads(content[0]["text"])
                                    err_msg = err_data.get("error", content[0]["text"])
                                except Exception:
                                    err_msg = content[0]["text"]
                            logger.error(f"FazerCards MCP Tool ошибка: {err_msg}")
                            return {"error": err_msg, "isError": True}

                        content = result.get("content", [])
                        if content and content[0].get("type") == "text":
                            try:
                                return json.loads(content[0]["text"])
                            except Exception:
                                return content[0]["text"]
                        return result
                    else:
                        text = await resp.text()
                        logger.error(f"FazerCards HTTP статус {resp.status}: {text[:200]}")
                        return {"error": f"HTTP {resp.status}: {text[:100]}", "isError": True}
        except asyncio.TimeoutError:
            logger.error("Таймаут соединения с сервером FazerCards (30 сек).")
            return {"error": "Timeout connecting to FazerCards", "isError": True}
        except Exception as exc:
            logger.error(f"Сетевая ошибка подключения к FazerCards: {exc}")
            return {"error": str(exc), "isError": True}

    async def search_catalog(self, query: str, item_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Ищет категории и игры в каталоге FazerCards по ключевому слову.
        query: название игры или продукта (минимум 2 символа).
        item_type: опционально 'gift_cards', 'game_keys' или 'topups'.
        Возвращает список словарей категорий: [{'type': ..., 'category_id': ..., 'name': ...}, ...]
        """
        clean_query = str(query).strip()
        if len(clean_query) < 2:
            return []

        args: Dict[str, Any] = {"query": clean_query}
        if item_type:
            args["type"] = item_type

        res = await self._call_tool("search_catalog", args)
        if isinstance(res, dict) and "results" in res and isinstance(res["results"], list):
            return res["results"]
        if isinstance(res, list):
            return res
        return []

    async def get_offers(self, item_type: str, category_id: str) -> Optional[Dict[str, Any]]:
        """
        Запрашивает список предложений (номиналы, цены и остатки) для указанной категории.
        item_type: 'gift_cards', 'game_keys' или 'topups'
        category_id: идентификатор категории (например, 'pubg_mobile' или 'pubg_mobile_auto')
        """
        return await self._call_tool("get_offers", {
            "type": item_type,
            "category_id": category_id
        })

    async def get_balance(self) -> Optional[Dict[str, Any]]:
        """Запрашивает текущий оптовый баланс аккаунта FazerCards."""
        return await self._call_tool("get_balance")

    async def get_account(self) -> Optional[Dict[str, Any]]:
        """Запрашивает данные профиля и тарифа FazerCards."""
        return await self._call_tool("get_account")
