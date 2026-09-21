"""Thin wrapper around the Mercado Libre REST API used by the MCP tools.

Reference: https://developers.mercadolibre.com.mx/en_us/api-docs-es
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from .auth import TokenStore, get_valid_access_token, refresh_tokens
from .config import API_BASE_URL, Settings


class MeliClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._http = httpx.Client(base_url=API_BASE_URL, timeout=30)
        self._tokens = get_valid_access_token(settings)

    @property
    def user_id(self) -> int:
        return self._tokens.user_id

    @property
    def site_id(self) -> str:
        return self._settings.site_id

    def close(self) -> None:
        self._http.close()

    # -- low level -----------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._tokens.access_token}"
        response = self._http.request(method, path, headers=headers, **kwargs)

        if response.status_code == 401:
            # Access token expired/invalid mid-session: refresh once and retry.
            self._tokens = refresh_tokens(self._settings, self._tokens.refresh_token)
            TokenStore(self._settings.token_path).save(self._tokens)
            headers["Authorization"] = f"Bearer {self._tokens.access_token}"
            response = self._http.request(method, path, headers=headers, **kwargs)

        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json)

    def _put(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("PUT", path, json=json)

    # -- catalog / public search -----------------------------------------

    def search_products(self, query: str, limit: int = 20, offset: int = 0) -> Any:
        return self._get(
            f"/sites/{self.site_id}/search",
            params={"q": query, "limit": limit, "offset": offset},
        )

    def get_item(self, item_id: str) -> Any:
        return self._get(f"/items/{item_id}")

    def get_item_performance(self, item_id: str) -> Any:
        """The `health` item field is deprecated; this is Mercado Libre's
        replacement for listing-quality score + met/pending objectives.
        Note: unlike every other item endpoint, this one is singular
        `/item/` rather than `/items/`."""
        return self._get(f"/item/{item_id}/performance")

    def get_items_performance(
        self, item_ids: list[str], max_workers: int = 8, on_progress: Callable[[int, int], None] | None = None
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """Fetch `/item/{id}/performance` for many items concurrently (there
        is no multiget for this endpoint, unlike the rest of the Items API).
        Returns ({item_id: performance_body}, {item_id: http_status}) --
        the second dict holds only the ids that errored, so callers can see
        *why* an id was skipped instead of it disappearing silently.
        """
        results: dict[str, Any] = {}
        errors: dict[str, int] = {}
        done = 0

        def fetch(item_id: str) -> tuple[str, Any | None, int | None]:
            try:
                return item_id, self.get_item_performance(item_id), None
            except httpx.HTTPStatusError as exc:
                return item_id, None, exc.response.status_code

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for item_id, perf, error_status in pool.map(fetch, item_ids):
                done += 1
                if perf is not None:
                    results[item_id] = perf
                else:
                    errors[item_id] = error_status
                if on_progress:
                    on_progress(done, len(item_ids))
        return results, errors

    def get_categories(self) -> Any:
        return self._get(f"/sites/{self.site_id}/categories")

    def get_category_attributes(self, category_id: str) -> Any:
        return self._get(f"/categories/{category_id}/attributes")

    # -- seller: listings -------------------------------------------------

    def list_my_items(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> Any:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return self._get(f"/users/{self.user_id}/items/search", params=params)

    def list_all_my_item_ids(self, status: str | None = None) -> list[str]:
        """Page through `list_my_items` and return every item id.

        The `/users/{id}/items/search` endpoint caps offset+limit at 1000
        results; sellers with more listings than that need the scroll API
        instead, which this helper does not implement.
        """
        ids: list[str] = []
        offset = 0
        page_size = 100
        while True:
            page = self.list_my_items(status=status, limit=page_size, offset=offset)
            results = page.get("results", [])
            ids.extend(results)
            total = page.get("paging", {}).get("total", len(ids))
            offset += len(results)
            if not results or offset >= total:
                break
        return ids

    def multiget_items(
        self, item_ids: list[str], attributes: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Fetch several items in one call each (Mercado Libre's `/items?ids=`
        multiget, max 20 ids per request). Skips ids that error individually.
        """
        attrs = ",".join(attributes) if attributes else None
        items: list[dict[str, Any]] = []
        for i in range(0, len(item_ids), 20):
            batch = item_ids[i : i + 20]
            params: dict[str, Any] = {"ids": ",".join(batch)}
            if attrs:
                params["attributes"] = attrs
            for entry in self._get("/items", params=params):
                if entry.get("code") == 200:
                    items.append(entry["body"])
        return items

    def create_item(self, item: dict[str, Any]) -> Any:
        return self._post("/items", json=item)

    def update_item(self, item_id: str, changes: dict[str, Any]) -> Any:
        return self._put(f"/items/{item_id}", json=changes)

    def update_price(self, item_id: str, price: float) -> Any:
        return self.update_item(item_id, {"price": price})

    def update_stock(self, item_id: str, quantity: int) -> Any:
        return self.update_item(item_id, {"available_quantity": quantity})

    def set_item_status(self, item_id: str, status: str) -> Any:
        """status: 'active', 'paused' or 'closed'."""
        return self.update_item(item_id, {"status": status})

    # -- seller: orders -----------------------------------------------------

    def list_orders(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> Any:
        params: dict[str, Any] = {"seller": self.user_id, "limit": limit, "offset": offset}
        if status:
            params["order.status"] = status
        return self._get("/orders/search", params=params)

    def get_order(self, order_id: str) -> Any:
        return self._get(f"/orders/{order_id}")

    # -- seller: questions & messaging --------------------------------------

    def list_questions(self, status: str = "UNANSWERED", limit: int = 50) -> Any:
        return self._get(
            "/questions/search",
            params={"seller_id": self.user_id, "status": status, "limit": limit},
        )

    def answer_question(self, question_id: int, text: str) -> Any:
        return self._post("/answers", json={"question_id": question_id, "text": text})

    def list_order_messages(self, order_id: str) -> Any:
        return self._get(
            f"/messages/orders/{order_id}",
            params={"tag": "post_sale", "seller_id": self.user_id},
        )

    def send_order_message(self, order_id: str, text: str) -> Any:
        return self._post(
            f"/messages/orders/{order_id}",
            json={
                "from": {"user_id": str(self.user_id)},
                "text": text,
            },
        )

    # -- seller: reputation ---------------------------------------------------

    def get_user(self, user_id: int | None = None) -> Any:
        return self._get(f"/users/{user_id or self.user_id}")

    def get_my_reputation(self) -> Any:
        user = self.get_user()
        return user.get("seller_reputation")
