"""Thin wrapper around the Mercado Libre REST API used by the MCP tools.

Reference: https://developers.mercadolibre.com.mx/en_us/api-docs-es
"""

from __future__ import annotations

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
