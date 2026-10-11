"""WSGI application, deployed with Gunicorn behind HTTPS and Shopify's app proxy."""
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from security import ADMIN_EMAIL, Denied, SessionStore, verify_proxy
from shopify import Shopify, ShopifyError

ROOT = Path(__file__).parent


def gid(value, kind):
    if not isinstance(value, str) or not re.fullmatch(r"gid://shopify/" + kind + r"/[0-9]+", value):
        raise ValueError("Ungültige Shopify-ID.")
    return value


def text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError("Eingabe ist zu lang oder ungültig.")
    return value


PRODUCTS = """query($after:String) { products(first:25,after:$after,sortKey:UPDATED_AT,reverse:true) {
 pageInfo { hasNextPage endCursor } nodes { id title status vendor productType tags
 variants(first:100) { pageInfo { hasNextPage } nodes { id title price sku inventoryItem { id tracked
 inventoryLevels(first:20) { pageInfo { hasNextPage } nodes { location { id name } quantities(names:["available"]) { name quantity } } } } } } } } }"""
ORDERS = """query($after:String) { orders(first:25,after:$after,sortKey:CREATED_AT,reverse:true) {
 pageInfo { hasNextPage endCursor } nodes { id name createdAt displayFinancialStatus displayFulfillmentStatus note tags
 currentTotalPriceSet { shopMoney { amount currencyCode } } } } }"""


class App:
    def __init__(self, config=None, shopify=None, sessions=None):
        self.config = dict(os.environ if config is None else config)
        self.shop = self.config.get("SHOPIFY_SHOP", "")
        self.owner = self.config.get("ADMIN_CUSTOMER_ID", "")
        self.prefix = self.config.get("APP_PROXY_PATH", "/apps/plotwerk-admin")
        secret = self.config.get("SHOPIFY_CLIENT_SECRET", "")
        encoded = self.config.get("ADMIN_PASSWORD_HASH", "")
        self.ready = bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", self.shop)
            and re.fullmatch(r"[0-9]+", self.owner) and secret and self.config.get("SHOPIFY_CLIENT_ID")
            and re.fullmatch(r"scrypt\$[0-9a-f]{32}\$[0-9a-f]{128}", encoded)
            and re.fullmatch(r"/apps/[a-z0-9-]+", self.prefix))
        self.api = shopify or Shopify(self.shop, self.config.get("SHOPIFY_CLIENT_ID", ""), secret)
        db_path = self.config.get("DATABASE_PATH", "data/admin.sqlite3")
        database_url = self.config.get("DATABASE_URL", "")
        if self.config.get("REQUIRE_POSTGRES") == "true" and not database_url and sessions is None:
            raise RuntimeError("Dauerhafte PostgreSQL-Datenbank fehlt. DATABASE_URL konfigurieren.")
        if sessions is None and not database_url:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.sessions = sessions or SessionStore(db_path, database_url)

    def owner_identity(self):
        customer = self.api.graphql("query($id:ID!) { customer(id:$id) { id defaultEmailAddress { emailAddress } } }", {"id": f"gid://shopify/Customer/{self.owner}"}).get("customer")
        email = ((customer or {}).get("defaultEmailAddress") or {}).get("emailAddress", "")
        if not customer or email.strip().lower() != ADMIN_EMAIL:
            raise Denied("Zugriff verweigert.")

    def dispatch(self, action, body):
        if action == "products":
            return self.api.graphql(PRODUCTS, {"after": body.get("after")})
        if action == "orders":
            return self.api.graphql(ORDERS, {"after": body.get("after")})
        if action == "product.save":
            status = body.get("status")
            if status not in ("ACTIVE", "DRAFT", "ARCHIVED"):
                raise ValueError("Ungültiger Produktstatus.")
            title = text(body.get("title"), 255).strip()
            if not title:
                raise ValueError("Titel fehlt.")
            value = {"id": gid(body.get("id"), "Product"), "title": title, "status": status,
                     "vendor": text(body.get("vendor", ""), 255), "productType": text(body.get("productType", ""), 255)}
            result = self.api.graphql("mutation($product:ProductUpdateInput!) { productUpdate(product:$product) { product { id } userErrors { message } } }", {"product": value})
        elif action == "variant.price":
            raw_price = text(body.get("price"), 30)
            try:
                price = Decimal(raw_price)
                if not price.is_finite() or price < 0 or price > 99999999 or price.as_tuple().exponent < -2:
                    raise ValueError("Preis muss positiv sein und höchstens zwei Nachkommastellen haben.")
            except InvalidOperation:
                raise ValueError("Ungültiger Preis.") from None
            value = {"productId": gid(body.get("productId"), "Product"), "variants": [{"id": gid(body.get("id"), "ProductVariant"), "price": str(price)}]}
            result = self.api.graphql("mutation($productId:ID!,$variants:[ProductVariantsBulkInput!]!) { productVariantsBulkUpdate(productId:$productId,variants:$variants) { productVariants { id price } userErrors { message } } }", value)
        elif action == "inventory.set":
            quantity, before = body.get("quantity"), body.get("before")
            if type(quantity) is not int or type(before) is not int or not 0 <= quantity <= 1000000:
                raise ValueError("Ungültiger Bestand.")
            key = text(body.get("requestId"), 36)
            if not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", key):
                raise ValueError("Ungültige Änderungskennung.")
            value = {"key": key, "input": {"name": "available", "reason": "correction", "quantities": [{"inventoryItemId": gid(body.get("inventoryItemId"), "InventoryItem"), "locationId": gid(body.get("locationId"), "Location"), "quantity": quantity, "compareQuantity": before}]}}
            result = self.api.graphql("mutation($input:InventorySetQuantitiesInput!,$key:String!) { inventorySetQuantities(input:$input) @idempotent(key:$key) { inventoryAdjustmentGroup { createdAt } userErrors { message } } }", value)
        elif action == "order.note":
            value = {"input": {"id": gid(body.get("id"), "Order"), "note": text(body.get("note", ""), 2000)}}
            result = self.api.graphql("mutation($input:OrderInput!) { orderUpdate(input:$input) { order { id note } userErrors { message } } }", value)
        else:
            raise ValueError("Unbekannte Aktion.")
        self.sessions.audit(self.owner, action, str(body.get("id", body.get("inventoryItemId", ""))))
        return result

    def __call__(self, environ, start_response):
        code, output, mime = 200, {}, "application/json; charset=utf-8"
        try:
            method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "")
            if method == "GET" and path == "/healthz":
                output = {"status": "running", "configured": self.ready}
            else:
                if not self.ready:
                    raise RuntimeError("Adminbereich ist noch nicht eingerichtet.")
                verify_proxy(environ.get("QUERY_STRING", ""), self.config["SHOPIFY_CLIENT_SECRET"], self.shop, self.owner, self.prefix)
                if method == "GET" and path in ("/proxy", "/proxy/"):
                    self.owner_identity()
                    output = (ROOT / "web/index.html").read_text().replace("__PROXY_PATH__", self.prefix)
                    mime = "text/html; charset=utf-8"
                elif method == "GET" and path in ("/proxy/admin.js", "/proxy/admin.css"):
                    filename = path.rsplit("/", 1)[1]
                    output = (ROOT / "web" / filename).read_text()
                    mime = "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
                elif method == "POST" and path == "/proxy/api":
                    if environ.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
                        raise Denied("Zugriff verweigert.")
                    length = int(environ.get("CONTENT_LENGTH") or 0)
                    if not 0 < length <= 16384:
                        raise ValueError("Ungültige Anfragegröße.")
                    body = json.loads(environ["wsgi.input"].read(length))
                    if not isinstance(body, dict):
                        raise ValueError("Ungültige Anfrage.")
                    action = body.get("action")
                    if action == "login":
                        self.owner_identity()
                        token = self.sessions.login(self.owner, body.get("password", ""), self.config["ADMIN_PASSWORD_HASH"])
                        output = {"session": token, "expiresIn": 1800}
                    else:
                        token = body.get("session")
                        self.sessions.check(token, self.owner)
                        self.owner_identity()
                        if action == "logout":
                            self.sessions.logout(token)
                            output = {"ok": True}
                        else:
                            output = self.dispatch(action, body)
                else:
                    code, output = 404, {"error": "Nicht gefunden."}
        except Denied as error:
            code, output = 403, {"error": str(error)}
        except (ValueError, TypeError, KeyError):
            code, output = 400, {"error": "Ungültige Anfrage. Eingaben prüfen."}
        except ShopifyError as error:
            code, output = 502, {"error": str(error)}
        except RuntimeError as error:
            code, output = 503, {"error": str(error)}
        except Exception:
            code, output = 500, {"error": "Serverfehler. Bitte neu laden."}
        encoded = (json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else output).encode()
        statuses = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}
        start_response(f"{code} {statuses[code]}", [("Content-Type", mime), ("Content-Length", str(len(encoded))), ("Cache-Control", "no-store, private"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")])
        return [encoded]


application = App()
