"""Server-side Shopify Admin API; credentials are never returned to the browser."""
import json
import logging
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ShopifyError(Exception):
    pass


class Shopify:
    def __init__(self, shop, client_id, client_secret):
        self.shop, self.client_id, self.client_secret = shop, client_id, client_secret
        self.token, self.expires = None, 0
        self.lock = threading.Lock()

    def request(self, path, body, token=None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["X-Shopify-Access-Token"] = token
        req = Request(f"https://{self.shop}{path}", data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urlopen(req, timeout=20) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError):
            raise ShopifyError("Shopify nicht erreichbar oder Berechtigung fehlt. Bitte den aktuellen Stand neu laden, bevor du eine Änderung erneut sendest.") from None

    def access_token(self):
        with self.lock:
            if not self.token or time.time() >= self.expires:
                data = self.request("/admin/oauth/access_token", {
                    "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret
                })
                if not data.get("access_token"):
                    raise ShopifyError("App-Installation und Shopify-Zugangsdaten prüfen.")
                self.token = data["access_token"]
                self.expires = time.time() + int(data.get("expires_in", 3600)) - 60
            return self.token

    def graphql(self, query, variables=None):
        data = self.request("/admin/api/2026-10/graphql.json", {"query": query, "variables": variables or {}}, self.access_token())
        if data.get("errors"):
            # Only fixed diagnostic codes, never query values, tokens or customer data.
            allowed = {"MAX_COST_EXCEEDED", "THROTTLED", "ACCESS_DENIED", "GRAPHQL_VALIDATION_FAILED"}
            codes = [(error.get("extensions") or {}).get("code") for error in data["errors"] if isinstance(error, dict)]
            logging.warning("Shopify GraphQL rejected request: %s", ",".join(code if code in allowed else "OTHER" for code in codes))
            raise ShopifyError("Shopify-Anfrage fehlgeschlagen. API-Berechtigungen und Version prüfen.")
        result = data.get("data")
        if not isinstance(result, dict):
            raise ShopifyError("Ungültige Shopify-Antwort.")
        for value in result.values():
            if isinstance(value, dict) and value.get("userErrors"):
                raise ShopifyError("; ".join(e["message"] for e in value["userErrors"]))
        return result
