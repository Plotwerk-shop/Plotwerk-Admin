"""Durable mail jobs; SMTP accepted is distinct from recipient delivery.
An ambiguous in-flight job is never automatically sent a second time.
"""
import email
import hashlib
import hmac
import imaplib
import json
import re
import smtplib
import ssl
import time
import uuid
from email.message import EmailMessage
from email.policy import default
from email.utils import parseaddr
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from features import bounded
from security import Denied


def address(value):
    value = bounded(value, 254).strip().lower()
    if not re.fullmatch(r'[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+', value):
        raise ValueError('Ungültige E-Mail-Adresse.')
    return value


class Mailroom:
    def __init__(self, app):
        self.app, self.store, self.config = app, app.sessions, app.config
        with self.store.connect() as db:
            for statement in (
                'CREATE TABLE IF NOT EXISTS mail_drafts (id TEXT PRIMARY KEY, subject TEXT, body TEXT, kind TEXT, source TEXT, status TEXT, cursor TEXT, created DOUBLE PRECISION)',
                'CREATE TABLE IF NOT EXISTS mail_jobs (id TEXT PRIMARY KEY, campaign TEXT, recipient TEXT, customer TEXT, subject TEXT, body TEXT, status TEXT, updated DOUBLE PRECISION, UNIQUE(campaign,recipient))',
                'CREATE TABLE IF NOT EXISTS mail_receipts (job TEXT PRIMARY KEY, provider TEXT, provider_id TEXT)',
                'CREATE TABLE IF NOT EXISTS mail_optouts (recipient TEXT PRIMARY KEY)',
                'CREATE TABLE IF NOT EXISTS mail_inbox (id TEXT PRIMARY KEY, sender TEXT, subject TEXT, body TEXT, received TEXT)',
            ):
                db.execute(statement)

    @property
    def ready(self):
        sender = bool(self.config.get('RESEND_API_KEY') and self.config.get('MAIL_FROM')) or all(self.config.get(k) for k in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD'))
        return sender and len(self.config.get('MAIL_SIGNING_KEY', '')) >= 32 and self.config.get('MAIL_PUBLIC_URL', '').startswith('https://')

    def dispatch(self, action, b):
        if action == 'mail.status':
            with self.store.connect() as db:
                drafts = db.execute('SELECT id,subject,body,kind,source,status,created FROM mail_drafts ORDER BY created DESC LIMIT 50').fetchall()
                counts = db.execute('SELECT campaign,status,count(*) FROM mail_jobs GROUP BY campaign,status').fetchall()
                inbox = db.execute('SELECT id,sender,subject,body,received FROM mail_inbox ORDER BY received DESC LIMIT 50').fetchall()
            return {'ready': self.ready, 'imapReady': all(self.config.get(k) for k in ('IMAP_HOST', 'IMAP_USER', 'IMAP_PASSWORD')),
                    'drafts': [dict(zip(('id','subject','body','kind','source','status','created'), r)) for r in drafts],
                    'counts': [dict(zip(('campaign','status','count'), r)) for r in counts],
                    'inbox': [dict(zip(('id','sender','subject','body','received'), r)) for r in inbox]}
        if action == 'mail.sync':
            return self.sync()
        if action == 'mail.draft':
            kind = b.get('kind')
            if kind not in ('newsletter', 'reply'):
                raise ValueError('Ungültige Nachricht.')
            source = bounded(b.get('source', ''), 200)
            if kind == 'reply':
                with self.store.connect() as db:
                    if not db.execute('SELECT id FROM mail_inbox WHERE id=?', (source,)).fetchone():
                        raise ValueError('Anfrage nicht vorhanden.')
            subject, body = bounded(b.get('subject', ''), 200).strip(), bounded(b.get('body', ''), 10000).strip()
            if not subject or '\n' in subject or '\r' in subject or not body:
                raise ValueError('Betreff und Nachricht erforderlich.')
            draft = str(uuid.uuid4())
            with self.store.connect() as db:
                db.execute('INSERT INTO mail_drafts VALUES (?,?,?,?,?,?,?,?)', (draft,subject,body,kind,source,'draft',None,time.time()))
            return {'id': draft}
        if action == 'mail.approve':
            if not self.ready:
                raise RuntimeError('Maildienst und Abmelde-URL zuerst in Render konfigurieren.')
            with self.store.connect() as db:
                self.store.lock_owner(db, 'mail-approve')
                draft = db.execute('SELECT subject,body,kind,source,status FROM mail_drafts WHERE id=?', (b.get('id'),)).fetchone()
                if not draft or draft[4] != 'draft':
                    raise ValueError('Entwurf bereits freigegeben oder nicht vorhanden.')
                if draft[2] == 'reply':
                    row = db.execute('SELECT sender FROM mail_inbox WHERE id=?', (draft[3],)).fetchone()
                    if not row:
                        raise ValueError('Anfrage fehlt.')
                    db.execute('INSERT INTO mail_jobs VALUES (?,?,?,?,?,?,?,?)', (str(uuid.uuid4()),b['id'],address(row[0]),None,draft[0],draft[1],'queued',time.time()))
                    status = 'queued'
                else:
                    status = 'collecting'
                db.execute('UPDATE mail_drafts SET status=? WHERE id=?', (status,b['id']))
            return {'ok': True}
        if action == 'mail.cancel':
            with self.store.connect() as db:
                db.execute("UPDATE mail_drafts SET status='cancelled' WHERE id=?", (b.get('id'),))
                db.execute("UPDATE mail_jobs SET status='cancelled' WHERE campaign=? AND status='queued'", (b.get('id'),))
            return {'ok': True}
        if action == 'mail.run':
            return self.run()
        return None

    def sync(self):
        if not all(self.config.get(k) for k in ('IMAP_HOST','IMAP_USER','IMAP_PASSWORD')):
            raise RuntimeError('IMAP-Postfach zuerst in Render konfigurieren.')
        imported = 0
        with imaplib.IMAP4_SSL(self.config['IMAP_HOST'], port=993, ssl_context=ssl.create_default_context(), timeout=20) as mailbox:
            mailbox.login(self.config['IMAP_USER'], self.config['IMAP_PASSWORD'])
            status, _ = mailbox.select('INBOX', readonly=True)
            if status != 'OK':
                raise RuntimeError('Postfach konnte nicht geöffnet werden.')
            validity = str(mailbox.response('UIDVALIDITY')[1][0])
            status, items = mailbox.uid('search', None, 'ALL')
            if status != 'OK':
                raise RuntimeError('Postfachabfrage fehlgeschlagen.')
            for uid in items[0].split()[-30:]:
                key = hashlib.sha256((self.config['IMAP_HOST'] + self.config['IMAP_USER'] + validity + uid.decode()).encode()).hexdigest()
                with self.store.connect() as db:
                    if db.execute('SELECT id FROM mail_inbox WHERE id=?', (key,)).fetchone():
                        continue
                status, data = mailbox.uid('fetch', uid, '(RFC822.SIZE)')
                size = re.search(rb'RFC822.SIZE (\d+)', b' '.join(x for x in data if isinstance(x, bytes))) if status == 'OK' else None
                if not size or int(size[1]) > 200000:
                    continue
                status, data = mailbox.uid('fetch', uid, '(BODY.PEEK[])')
                if status != 'OK':
                    continue
                raw = next((x[1] for x in data if isinstance(x, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw, policy=default)
                try:
                    sender = address(parseaddr(str(msg.get('From','')))[1])
                    if sender == self.config.get('SMTP_USER', '').lower():
                        continue
                except ValueError:
                    continue
                part = msg.get_body(preferencelist=('plain',)) if msg.is_multipart() else msg
                body = part.get_content() if part and part.get_content_type() == 'text/plain' else '[Diese Nachricht enthält keinen Klartext. Bitte im Mailprogramm öffnen.]'
                with self.store.connect() as db:
                    db.execute('INSERT INTO mail_inbox VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING', (key,sender,str(msg.get('Subject',''))[:200],str(body)[:10000],str(msg.get('Date',''))[:100]))
                imported += 1
        return {'imported': imported}

    def signature(self, recipient):
        return hmac.new(self.config['MAIL_SIGNING_KEY'].encode(), recipient.encode(), hashlib.sha256).hexdigest()

    def unsubscribe(self, recipient, signature):
        recipient = address(recipient)
        if not self.ready or not hmac.compare_digest(self.signature(recipient), signature):
            raise Denied('Ungültiger Abmeldelink.')
        with self.store.connect() as db:
            db.execute('INSERT INTO mail_optouts VALUES (?) ON CONFLICT(recipient) DO NOTHING', (recipient,))
            db.execute("UPDATE mail_jobs SET status='skipped' WHERE recipient=? AND customer IS NOT NULL AND status='queued'", (recipient,))
        # Local suppression always takes effect, independent of Shopify write permissions.
        return {'ok': True, 'message': 'Du erhältst keine weiteren Newsletter aus dem Plotwerk-Adminbereich.'}

    def run(self):
        if not self.ready:
            raise RuntimeError('Maildienst ist noch nicht eingerichtet.')
        # Collect one Shopify subscriber page per invocation: durable cursor, no arbitrary recipient list.
        with self.store.connect() as db:
            self.store.lock_owner(db, 'mail-worker')
            draft = db.execute("SELECT id,subject,body,cursor FROM mail_drafts WHERE status='collecting' ORDER BY created LIMIT 1").fetchone()
            if draft:
                result = self.app.api.graphql('''query($after:String) { customers(first:50,after:$after) { pageInfo { hasNextPage endCursor } nodes { id defaultEmailAddress { emailAddress marketingState } } } }''', {'after': draft[3]})['customers']
                for customer in result['nodes']:
                    info = customer.get('defaultEmailAddress') or {}
                    if info.get('marketingState') != 'SUBSCRIBED':
                        continue
                    try:
                        recipient = address(info.get('emailAddress'))
                    except (ValueError, TypeError):
                        continue
                    if db.execute('SELECT recipient FROM mail_optouts WHERE recipient=?', (recipient,)).fetchone():
                        continue
                    db.execute('INSERT INTO mail_jobs VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(campaign,recipient) DO NOTHING', (str(uuid.uuid4()),draft[0],recipient,customer['id'],draft[1],draft[2],'queued',time.time()))
                db.execute('UPDATE mail_drafts SET cursor=?,status=? WHERE id=?', (result['pageInfo']['endCursor'],'collecting' if result['pageInfo']['hasNextPage'] else 'queued',draft[0]))
        sent = 0
        daily_limit = max(1, min(10000, int(self.config.get('MAIL_DAILY_LIMIT', '100'))))
        monthly_limit = max(1, min(1000000, int(self.config.get('MAIL_MONTHLY_LIMIT', '3000'))))
        # Bounded batches fit worker/request deadlines. Never retry ambiguous SMTP acceptance.
        for _ in range(2):
            with self.store.connect() as db:
                self.store.lock_owner(db, 'mail-worker')
                db.execute("UPDATE mail_jobs SET status='uncertain' WHERE status='sending' AND updated<?", (time.time()-600,))
                used = db.execute("SELECT count(*) FROM mail_jobs WHERE status IN ('accepted','sending','uncertain') AND updated>?", (time.time()-86400,)).fetchone()[0]
                monthly = db.execute("SELECT count(*) FROM mail_jobs WHERE status IN ('accepted','sending','uncertain') AND updated>?", (time.time()-30*86400,)).fetchone()[0]
                if used >= daily_limit or monthly >= monthly_limit:
                    break
                row = db.execute("SELECT j.id,j.recipient,j.customer,j.subject,j.body FROM mail_jobs j JOIN mail_drafts d ON d.id=j.campaign WHERE j.status='queued' AND d.status='queued' ORDER BY j.updated LIMIT 1").fetchone()
                if not row:
                    break
                db.execute("UPDATE mail_jobs SET status='sending',updated=? WHERE id=?", (time.time(),row[0]))
            status = 'uncertain'
            try:
                allowed = True
                if row[2]:
                    customer = self.app.api.graphql('query($id:ID!) { customer(id:$id) { defaultEmailAddress { emailAddress marketingState } } }', {'id':row[2]}).get('customer') or {}
                    info = customer.get('defaultEmailAddress') or {}
                    with self.store.connect() as db:
                        optedout = db.execute('SELECT recipient FROM mail_optouts WHERE recipient=?', (row[1],)).fetchone()
                    allowed = info.get('marketingState') == 'SUBSCRIBED' and info.get('emailAddress','').lower() == row[1] and not optedout
                if not allowed:
                    status = 'skipped'
                else:
                    msg = EmailMessage()
                    msg['From'] = address(self.config.get('MAIL_FROM') or self.config['SMTP_USER']); msg['To'] = row[1]; msg['Subject'] = row[3]
                    msg['Message-ID'] = '<' + row[0] + '@' + self.app.shop + '>'
                    body = row[4]
                    if row[2]:
                        url = self.config['MAIL_PUBLIC_URL'].rstrip('/') + '/unsubscribe?' + urlencode({'email':row[1],'sig':self.signature(row[1])})
                        msg['List-Unsubscribe'] = '<' + url + '>'
                        body += '\n\nNewsletter abmelden: ' + url
                    msg.set_content(body)
                    if self.config.get('RESEND_API_KEY'):
                        payload = {'from': str(msg['From']), 'to': [row[1]], 'subject': row[3], 'text': body,
                                   'headers': {'Message-ID': str(msg['Message-ID'])}}
                        if row[2]:
                            payload['headers']['List-Unsubscribe'] = str(msg['List-Unsubscribe'])
                        request = Request('https://api.resend.com/emails', data=json.dumps(payload).encode(), method='POST',
                                          headers={'Authorization': 'Bearer ' + self.config['RESEND_API_KEY'], 'Content-Type': 'application/json', 'Idempotency-Key': 'plotwerk/' + row[0]})
                        try:
                            with urlopen(request, timeout=15) as response:
                                receipt = json.load(response)
                            if not isinstance(receipt.get('id'), str):
                                raise ValueError('Invalid receipt')
                            with self.store.connect() as db:
                                db.execute('INSERT INTO mail_receipts VALUES (?,?,?) ON CONFLICT(job) DO NOTHING', (row[0],'resend',receipt['id']))
                            status = 'accepted'
                        except HTTPError as error:
                            status = 'failed' if error.code in (400,401,403,422,429) else 'uncertain'
                    else:
                        with smtplib.SMTP_SSL(self.config['SMTP_HOST'], int(self.config.get('SMTP_PORT','465')), timeout=8, context=ssl.create_default_context()) as server:
                            server.login(self.config['SMTP_USER'],self.config['SMTP_PASSWORD'])
                            refused = server.send_message(msg)
                            status = 'failed' if refused else 'accepted'
                    if status == 'accepted': sent += 1
            except Exception:
                # No secret/server exception is returned to the client. Status requires manual review.
                status = 'uncertain'
            with self.store.connect() as db:
                db.execute('UPDATE mail_jobs SET status=?,updated=? WHERE id=?', (status,time.time(),row[0]))
        with self.store.connect() as db:
            db.execute("UPDATE mail_drafts SET status='done' WHERE status='queued' AND NOT EXISTS (SELECT 1 FROM mail_jobs WHERE campaign=mail_drafts.id AND status IN ('queued','sending'))")
        return {'accepted': sent}
