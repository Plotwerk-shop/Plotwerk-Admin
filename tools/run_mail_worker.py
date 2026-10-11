"""Run from an external scheduler, with secrets in its environment, never in URLs."""
import json
import os
from urllib.request import Request, urlopen

endpoint = os.environ['MAIL_PUBLIC_URL'].rstrip('/') + '/mail-worker'
if not endpoint.startswith('https://'):
    raise SystemExit('HTTPS required')
request = Request(endpoint, data=b'', method='POST', headers={'Authorization': 'Bearer ' + os.environ['MAIL_WORKER_KEY']})
with urlopen(request, timeout=90) as response:
    print(json.load(response))
