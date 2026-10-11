import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

from app import App
from security import Denied, SessionStore, password_hash, password_matches, verify_proxy
from shopify import Shopify, ShopifyError

SECRET = 'test-only-proxy-secret'
PASSWORD = 'a-long-test-password-only'


def signed_query(owner='42', **overrides):
    params = {'shop': 'test-shop.myshopify.com', 'logged_in_customer_id': owner,
              'path_prefix': '/apps/plotwerk-admin', 'timestamp': str(int(time.time()))}
    params.update(overrides)
    message = ''.join(sorted(f'{k}={v}' for k, v in params.items()))
    params['signature'] = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


class FakeShopify:
    def __init__(self):
        self.calls = []
        self.email = 'info@plotwerk-shop.de'

    def graphql(self, query, variables=None):
        self.calls.append((query, variables))
        if 'customer(id:' in query:
            return {'customer': {'id': 'gid://shopify/Customer/42', 'defaultEmailAddress': {'emailAddress': self.email}}}
        if query.startswith('query'):
            return {'products': {'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}
        return {'result': {'userErrors': []}}


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded = password_hash(PASSWORD)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(str(Path(self.temp.name) / 'test.sqlite3'))
        self.api = FakeShopify()
        self.config = {'SHOPIFY_SHOP': 'test-shop.myshopify.com', 'SHOPIFY_CLIENT_ID': 'test-id',
                       'SHOPIFY_CLIENT_SECRET': SECRET, 'ADMIN_CUSTOMER_ID': '42', 'ADMIN_PASSWORD_HASH': self.encoded}
        self.app = App(self.config, self.api, self.store)

    def request(self, body=None, owner='42', query=None, path='/proxy/api', method='POST', content_type='application/json'):
        payload = json.dumps(body or {}).encode()
        environ = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'QUERY_STRING': signed_query(owner) if query is None else query,
                   'CONTENT_TYPE': content_type, 'CONTENT_LENGTH': str(len(payload)), 'wsgi.input': io.BytesIO(payload)}
        result = {}
        def start(status, headers):
            result['status'], result['headers'] = int(status.split()[0]), dict(headers)
        raw = b''.join(self.app(environ, start))
        result['body'] = json.loads(raw) if result['headers']['Content-Type'].startswith('application/json') else raw.decode()
        return result

    def login(self):
        result = self.request({'action': 'login', 'password': PASSWORD})
        self.assertEqual(result['status'], 200)
        return result['body']['session']

    def test_extension_actions_cannot_bypass_owner_or_session(self):
        for action in ('product.edit','product.create','variant.edit','collection.edit','collection.add','discount.create','ticker.save','label','mail.status','mail.sync','mail.draft','mail.approve','mail.run'):
            self.assertEqual(self.request({'action':action})['status'],403)
            self.assertEqual(self.request({'action':action},owner='43')['status'],403)
        self.assertEqual(self.api.calls,[])

    def test_external_worker_requires_separate_secret(self):
        self.assertEqual(self.request(path='/mail-worker',query='')['status'],403)
        self.assertEqual(self.api.calls,[])

    def test_new_read_features_require_session_and_owner(self):
        for action in ('overview', 'collections'):
            self.assertEqual(self.request({'action': action})['status'], 403)
            self.assertEqual(self.request({'action': action}, owner='43')['status'], 403)
        self.assertEqual(self.api.calls, [])
        token = self.login()
        for action in ('overview', 'collections'):
            self.assertEqual(self.request({'action': action, 'session': token})['status'], 200)
        queries = [query for query, _ in self.api.calls]
        self.assertTrue(any('products(first:100)' in query and 'orders(first:50' in query for query in queries))
        self.assertTrue(any('collections(first:25' in query for query in queries))

    def test_collection_cursor_is_bound_not_inserted_into_query(self):
        token = self.login()
        cursor = 'untrusted" query'
        result = self.request({'action': 'collections', 'session': token, 'after': cursor})
        self.assertEqual(result['status'], 200)
        query, values = self.api.calls[-1]
        self.assertNotIn(cursor, query)
        self.assertEqual(values, {'after': cursor})

    def test_anonymous_and_other_customer_never_reach_shopify(self):
        for owner in ('', '43'):
            self.assertEqual(self.request({'action': 'login', 'password': PASSWORD}, owner=owner)['status'], 403)
        self.assertEqual(self.api.calls, [])

    def test_missing_tampered_stale_and_duplicate_signature_rejected(self):
        for query in ('', signed_query().replace('logged_in_customer_id=42', 'logged_in_customer_id=43'),
                      signed_query(timestamp='1'), signed_query() + '&logged_in_customer_id=42'):
            self.assertEqual(self.request(query=query)['status'], 403)
        self.assertEqual(self.api.calls, [])

    def test_verified_owner_email_required(self):
        self.api.email = 'someone@example.com'
        self.assertEqual(self.request({'action': 'login', 'password': PASSWORD})['status'], 403)

    def test_additional_password_and_session_required(self):
        self.assertEqual(self.request({'action': 'login', 'password': 'wrong-password-long'})['status'], 403)
        self.assertEqual(self.request({'action': 'products'})['status'], 403)
        token = self.login()
        response = self.request({'action': 'products', 'session': token})
        self.assertEqual(response['status'], 200)
        self.assertEqual(response['headers']['Cache-Control'], 'no-store, private')

    def test_password_attempt_limit_survives_store_reopen(self):
        for _ in range(5):
            with self.assertRaises(Denied): self.store.login('42', 'wrong-password-long', self.encoded)
        reopened = SessionStore(self.store.path)
        with self.assertRaises(Denied): reopened.login('42', PASSWORD, self.encoded)

    def test_session_expiry_and_logout(self):
        token = self.store.login('42', PASSWORD, self.encoded, now=100)
        with self.assertRaises(Denied): self.store.check(token, '42', now=1901)
        token = self.login()
        self.assertEqual(self.request({'action': 'logout', 'session': token})['status'], 200)
        self.assertEqual(self.request({'action': 'products', 'session': token})['status'], 403)

    def test_wrong_owner_and_simple_cross_site_post_rejected(self):
        token = self.login()
        self.assertEqual(self.request({'action': 'products', 'session': token}, owner='43')['status'], 403)
        self.assertEqual(self.request({'action': 'products', 'session': token}, content_type='text/plain')['status'], 403)

    def test_unconfigured_server_fails_closed(self):
        self.app = App({}, self.api, self.store)
        self.assertEqual(self.request()['status'], 503)
        self.assertEqual(self.api.calls, [])

    def test_static_page_does_not_leak_keys_and_is_owner_only(self):
        response = self.request(path='/proxy/', method='GET')
        self.assertEqual(response['status'], 200)
        self.assertNotIn(SECRET, response['body'])
        self.assertNotIn(self.encoded, response['body'])
        self.assertNotIn('__PROXY_PATH__', response['body'])
        self.assertEqual(self.request(path='/proxy/', method='GET', owner='43')['status'], 403)

    def test_inventory_compare_quantity_and_idempotency(self):
        token = self.login()
        body = {'action': 'inventory.set', 'session': token, 'inventoryItemId': 'gid://shopify/InventoryItem/1',
                'locationId': 'gid://shopify/Location/2', 'quantity': 7, 'before': 5,
                'requestId': '12345678-1234-1234-1234-123456789abc'}
        self.assertEqual(self.request(body)['status'], 200)
        query, variables = self.api.calls[-1]
        self.assertIn('@idempotent', query)
        self.assertEqual(variables['input']['quantities'][0]['compareQuantity'], 5)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT action FROM audit').fetchone()[0], 'inventory.set')

    def test_invalid_inputs_do_not_mutate_shopify(self):
        token = self.login()
        for price in ('-1', 'NaN', 'Infinity', '1.234'):
            response = self.request({'action': 'variant.price', 'session': token, 'productId': 'gid://shopify/Product/1', 'id': 'gid://shopify/ProductVariant/2', 'price': price})
            self.assertEqual(response['status'], 400)
        self.assertFalse(any(query.startswith('mutation') for query, _ in self.api.calls))

    def test_mutations_use_fixed_queries_and_variables(self):
        token = self.login()
        title = '<script>alert(1)</script>'
        response = self.request({'action': 'product.save', 'session': token, 'id': 'gid://shopify/Product/1', 'title': title, 'status': 'DRAFT'})
        self.assertEqual(response['status'], 200)
        query, variables = self.api.calls[-1]
        self.assertNotIn(title, query)
        self.assertEqual(variables['product']['title'], title)

    def test_shopify_user_errors_are_not_reported_as_success(self):
        api = Shopify('test-shop.myshopify.com', 'id', 'secret')
        api.access_token = lambda: 'test-token'
        api.request = lambda *args: {'data': {'inventorySetQuantities': {'userErrors': [{'message': 'Bestand hat sich geändert'}]}}}
        with self.assertRaisesRegex(ShopifyError, 'Bestand hat sich geändert'):
            api.graphql('mutation')

    def test_password_hash_and_documented_repeated_parameter_format(self):
        self.assertTrue(password_matches(PASSWORD, self.encoded))
        self.assertFalse(password_matches('other-password-long', self.encoded))
        # Shopify's sample uses a placeholder shop; compute the fixture for that literal placeholder.
        query = 'extra=1&extra=2&shop=%7Bshop%7D.myshopify.com&logged_in_customer_id=1&path_prefix=%2Fapps%2Fawesome_reviews&timestamp=1317327555&signature=e71d571c0a35d531ae367189d59afd33ae5b56be274c77c9e8bd6ce42e256304'
        self.assertEqual(verify_proxy(query, 'hush', '{shop}.myshopify.com', '1', '/apps/awesome_reviews', now=1317327555), '1')


if __name__ == '__main__':
    unittest.main()
