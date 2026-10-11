"""Run locally; enter password without exposing it in shell arguments/history."""
import getpass
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security import password_hash

password = getpass.getpass('Neues Admin-Passwort (mindestens 16 Zeichen): ')
if password != getpass.getpass('Passwort wiederholen: '):
    raise SystemExit('Passwörter stimmen nicht überein.')
print(password_hash(password))
