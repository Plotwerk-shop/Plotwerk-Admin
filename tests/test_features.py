import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import App
from features import amount
from security import Denied, SessionStore


class ShopifyFixture:
    def __init__(self):
        self.calls = []
        self.state = 'SUBSCRIBED'
        self.pages = 0

    def graphql(self, query, variables=None):
        self.calls.append((query, variables))
        if 'customers(first:50' in query:
            self.pages += 1
            return {'customers': {'nodes': [
                {'id': 'gid://shopify/Customer/1', 'defaultEmailAddress': {'emailAddress': 'first@example.org', 'marketingState': 'SUBSCRIBED'}},
                {'id': 'gid://shopify/Customer/2', 'defaultEmailAddress': {'emailAddress': 'blocked@example.org', 'marketingState': 'UNSUBSCRIBED'}}],
                'pageInfo': {'hasNextPage': self.pages == 1, 'endCursor': 'cursor1'}}}
        if 'customer(id:' in query:
            return {'customer': {'defaultEmailAddress': {'emailAddress': 'first@example.org', 'marketingState': self.state}}}
        if 'shop { id }' in query:
            return {'shop': {'id': 'gid://shopify/Shop/1'}}
        return {'ok': True}


class FeaturesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(str(Path(self.tmp.name)/'db.sqlite'))
        self.api = ShopifyFixture()
        self.config = {'SMTP_HOST':'test.invalid','SMTP_USER':'info@example.org','SMTP_PASSWORD':'fixture',
                       'MAIL_PUBLIC_URL':'https://test.invalid','MAIL_SIGNING_KEY':'x'*48}
        self.app = App(self.config,self.api,self.store)
        self.mail = self.app.mailroom

    def draft(self):
        draft = self.mail.dispatch('mail.draft', {'kind':'newsletter','subject':'Test','body':'Test message'})['id']
        self.mail.dispatch('mail.approve', {'id':draft})
        return draft

    def test_subscription_pages_are_durable_and_deduplicated(self):
        draft = self.draft()
        with patch('mailroom.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            self.mail.run()
            self.assertEqual(smtp.call_count, 0) # collect entire campaign first
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT cursor FROM mail_drafts WHERE id=?',(draft,)).fetchone()[0], 'cursor1')
            self.mail.run()
            self.assertEqual(smtp.call_count, 1)
        with self.store.connect() as db:
            jobs = db.execute('SELECT recipient,status FROM mail_jobs').fetchall()
        self.assertEqual(jobs, [('first@example.org','accepted')])

    def test_consent_is_rechecked_before_send(self):
        self.draft(); self.mail.run(); self.api.state = 'UNSUBSCRIBED'
        with patch('mailroom.smtplib.SMTP_SSL') as smtp:
            self.mail.run(); smtp.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mail_jobs').fetchone()[0], 'skipped')

    def test_signed_optout_is_persistent_and_not_an_open_endpoint(self):
        with self.assertRaises(Denied): self.mail.unsubscribe('first@example.org','invalid')
        self.mail.unsubscribe('first@example.org',self.mail.signature('first@example.org'))
        self.draft(); self.mail.run(); self.mail.run()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM mail_jobs').fetchone()[0], 0)

    def test_ambiguous_smtp_send_is_never_retried(self):
        self.draft(); self.mail.run()
        with patch('mailroom.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.side_effect = TimeoutError()
            self.mail.run(); self.mail.run()
            self.assertEqual(smtp.call_count,1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM mail_jobs').fetchone()[0], 'uncertain')

    def test_reply_recipient_is_derived_from_inbox_and_approval_not_repeatable(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO mail_inbox VALUES (?,?,?,?,?)',('msg','sender@example.org','Question','body','date'))
        draft = self.mail.dispatch('mail.draft',{'kind':'reply','source':'msg','subject':'Reply','body':'Answer','recipient':'attacker@example.org'})['id']
        self.mail.dispatch('mail.approve',{'id':draft})
        with self.assertRaises(ValueError): self.mail.dispatch('mail.approve',{'id':draft})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT recipient FROM mail_jobs').fetchone()[0],'sender@example.org')

    def test_no_unconfigured_or_arbitrary_mail(self):
        with self.assertRaises(ValueError): self.mail.dispatch('mail.draft',{'kind':'reply','source':'absent','subject':'Hello','body':'Test'})
        self.config.pop('SMTP_PASSWORD')
        app = App(self.config,self.api,self.store)
        draft = app.mailroom.dispatch('mail.draft',{'kind':'newsletter','subject':'Hello','body':'Test'})['id']
        with self.assertRaises(RuntimeError): app.mailroom.dispatch('mail.approve',{'id':draft})

    def test_cancellation_prevents_remaining_send(self):
        draft=self.draft();self.mail.run();self.mail.dispatch('mail.cancel',{'id':draft})
        with patch('mailroom.smtplib.SMTP_SSL') as smtp:
            self.mail.run();smtp.assert_not_called()

    def test_discount_validation_and_fixed_query(self):
        values={'title':'Three items','mode':'automatic','kind':'percent','value':'10','minimumQuantity':3}
        self.app.features.dispatch('discount.create',values)
        query,variables=self.api.calls[-1]
        self.assertIn('discountAutomaticBasicCreate',query)
        self.assertEqual(variables['input']['customerGets']['value']['percentage'],.1)
        self.assertEqual(variables['input']['minimumRequirement']['quantity']['greaterThanOrEqualToQuantity'],'3')
        for invalid in ('NaN','Infinity','101','-1'):
            with self.assertRaises(ValueError): self.app.features.dispatch('discount.create',{**values,'value':invalid})

    def test_variant_fields_and_description_preserved_in_variables(self):
        self.app.features.dispatch('product.edit',{'id':'gid://shopify/Product/1','title':'A','descriptionHtml':'<p>Example</p>','tags':'x, y','status':'DRAFT'})
        _,variables=self.api.calls[-1]
        self.assertEqual(variables['product']['descriptionHtml'],'<p>Example</p>')
        self.assertEqual(variables['product']['tags'],['x','y'])
        self.app.features.dispatch('variant.edit',{'id':'gid://shopify/ProductVariant/2','productId':'gid://shopify/Product/1','price':'3.40','sku':'PW1','barcode':'PW1','taxable':True,'inventoryPolicy':'DENY'})
        _,variables=self.api.calls[-1]
        self.assertEqual(variables['variants'][0]['inventoryItem']['sku'],'PW1')

    def test_ticker_internal_links_and_compare_digest(self):
        with self.assertRaises(ValueError): self.app.features.dispatch('ticker.save',{'text':'Hello','link':'//outside.invalid'})
        self.app.features.dispatch('ticker.save',{'text':'Hello','link':'/collections/all','enabled':True,'compareDigest':'abc'})
        _,v=self.api.calls[-1]
        self.assertEqual(v['fields'][0]['compareDigest'],'abc')

    def test_code128_is_generated_and_markup_input_rejected(self):
        result=self.app.features.dispatch('label',{'code':'PW-123'})
        import base64
        self.assertIn(b'<svg',base64.b64decode(result['svg']))
        with self.assertRaises(ValueError):self.app.features.dispatch('label',{'code':'\ninvalid'})

    def test_daily_limit_counts_ambiguous_messages(self):
        self.app.mailroom.config['MAIL_DAILY_LIMIT']='1'
        draft=self.draft();self.mail.run()
        with self.store.connect() as db:
            db.execute('INSERT INTO mail_jobs VALUES (?,?,?,?,?,?,?,?)',('old',draft,'old@example.org',None,'title','body','uncertain',time.time()))
        with patch('mailroom.smtplib.SMTP_SSL') as smtp:
            self.mail.run();smtp.assert_not_called()

    def test_https_mail_provider_records_id_and_idempotency(self):
        self.mail.config.update(RESEND_API_KEY='test-only-api-key',MAIL_FROM='info@example.org')
        self.draft();self.mail.run()
        import io
        with patch('mailroom.urlopen') as request, patch('mailroom.smtplib.SMTP_SSL') as smtp:
            request.return_value.__enter__.return_value = io.BytesIO(b'{"id":"provider-fixture"}')
            self.mail.run()
            smtp.assert_not_called()
            req=request.call_args[0][0]
            self.assertEqual(req.full_url,'https://api.resend.com/emails')
            self.assertTrue(req.get_header('Idempotency-key').startswith('plotwerk/'))
            payload=json.loads(req.data)
            self.assertEqual(payload['to'],['first@example.org'])
            self.assertIn('/unsubscribe?',payload['text'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT provider_id FROM mail_receipts').fetchone()[0],'provider-fixture')
