# Plotwerk Admin

Ein eigener Adminbereich unter `/apps/plotwerk-admin` im Shopify-Shop, mit dunklem Plotwerk-Design und pink-violetten Akzenten. Der Code ist eine erste Version, kein bereits eingerichteter oder live getesteter Dienst.

## Enthalten

- Bestehende Produkte bearbeiten: Titel, Marke, Produkttyp und Status.
- Preise je Variante direkt speichern.
- Verfügbaren Bestand je Variante und Standort ändern. Shopify prüft den vorherigen Bestand; Änderungen aus einer zwischenzeitlichen Bestellung werden nicht still überschrieben.
- Bestellungen der letzten 60 Tage ansehen und deren Notizen bearbeiten.
- Seitennavigation und Nachladen von jeweils 25 Produkten/Bestellungen.
- Zugriff nur für die fest konfigurierte Shopify-Kunden-ID und deren E-Mail `info@plotwerk-shop.de`, zusätzlich mit einem eigenen Admin-Passwort.

Noch nicht enthalten: neue Produkte erstellen, Produktbilder hochladen, Kollektionen verwalten, Rabatte, Versandetiketten, Erstattungen, Design-Editor oder vollständige Shopify-Verwaltung. Produktstatus „Aktiv“ allein veröffentlicht ein Produkt nicht automatisch in neuen Vertriebskanälen. Pro Produkt werden bis zu 100 Varianten und pro Variante bis zu 20 Standorte gezeigt; weitere Daten werden als solche kenntlich gemacht. Bestellstatus werden in dieser Version mit den Shopify-Statusbezeichnungen angezeigt.

## 1. Shopify-App vorbereiten

Die App muss im **gleichen Shopify-Unternehmen wie der eigene Shop** angelegt und im Shop installiert sein, damit der Client-Credentials-Grant funktioniert.

Benötigte Bereiche:

```text
write_products,write_inventory,read_locations,write_orders,read_customers,write_app_proxy
```

Die Abfrage der Kunden-E-Mail benötigt den entsprechenden Zugriff auf geschützte Kundendaten. Falls Shopify diesen nicht freigibt, verweigert die App den Zugang; sie ersetzt die Prüfung nicht durch eine ungeprüfte E-Mail aus dem Browser.

Notiere die echte `*.myshopify.com`-Adresse, Client-ID und Client-Secret. Geheimnisse ausschließlich beim Hoster eintragen. Niemals ins Repository oder in Theme-Dateien schreiben. Ein Kundenkonto mit `info@plotwerk-shop.de` muss existieren und sich erfolgreich im Onlineshop anmelden können.

## 2. Deinen Kunden eindeutig festlegen

Unter **Shopify → Kunden** dein Konto `info@plotwerk-shop.de` öffnen. Die Zahlenfolge hinter `/customers/` in der Verwaltungs-URL ist die `ADMIN_CUSTOMER_ID`. Dies ist **keine Mitarbeiter- oder Shopify-Nutzer-ID**. Die ID wird fest hinterlegt; andere Kunden erhalten auch bei gleicher oder geänderter E-Mail keinen Zugang.

Für das zusätzliche Passwort lokal ausführen (Python 3.13 empfohlen):

```bash
python3 tools/hash_password.py
```

Das Passwort zweimal verdeckt eingeben. Den ausgegebenen Hash als `ADMIN_PASSWORD_HASH` beim Hoster speichern. Das Passwort selbst wird nicht gespeichert. Nach einem Passwortwechsel den Dienst neu starten und alle Sessions durch Löschen der `sessions`-Tabelle widerrufen; diese Version hat noch keine Passwortwechsel-Oberfläche.

## 3. Hosting

Benötigt werden HTTPS, Python 3.13 oder Docker und ein **dauerhaftes, gesichertes Datenverzeichnis** für SQLite. Eine einzige Instanz verwenden. Sessions, Anmeldeversuche und Änderungsprotokoll werden darin abgelegt. Keine API-Schlüssel oder Passwörter werden protokolliert. Zugangsdaten und Query-Strings dürfen auch in den Logs des vorgeschalteten Hosters nicht öffentlich erscheinen.

Beispiel mit Render: **New → Web Service → dieses GitHub-Repository verbinden**, Python wählen:

| Einstellung | Wert |
| --- | --- |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60 app:application` |
| Health Check | `/healthz` |
| Dauerhaftes Laufwerk | Mount-Pfad `/data` |
| `DATABASE_PATH` | `/data/admin.sqlite3` |

Bei Render braucht ein dauerhaftes Laufwerk einen kostenpflichtigen Dienst. Preise vor der Buchung prüfen. Das Repository startet oder bezahlt keinen Hosting-Dienst. Alternativ Docker hinter einem HTTPS-Reverse-Proxy betreiben, `/data` als dauerhaftes Volume einbinden und für UID 10001 schreibbar machen.

Diese Umgebungsvariablen **als Secret-Einstellungen** setzen:

| Name | Inhalt |
| --- | --- |
| `SHOPIFY_SHOP` | Echte Shop-Adresse, z. B. `dein-shop.myshopify.com`, ohne `https://` |
| `SHOPIFY_CLIENT_ID` | Client-ID deiner eigenen App |
| `SHOPIFY_CLIENT_SECRET` | Client-Secret deiner eigenen App |
| `ADMIN_CUSTOMER_ID` | Numerische ID deines Kundenkontos |
| `ADMIN_PASSWORD_HASH` | Ausgabe des Passwort-Tools |
| `APP_PROXY_PATH` | `/apps/plotwerk-admin` |
| `DATABASE_PATH` | `/data/admin.sqlite3` |

Die `.env.example` ist nur eine Vorlage; `.env` wird nicht automatisch eingelesen. Ohne gültige Pflichtangaben bleibt der Dienst gesperrt. `/healthz` ist ein öffentlicher, datenfreier Prozesscheck; `configured: true` bestätigt nur die Konfiguration, **nicht** eine erfolgreiche Shopify-Verbindung.

## 4. App-Proxy verbinden

In der Shopify-App den App-Proxy konfigurieren:

| Feld | Wert |
| --- | --- |
| Prefix | `apps` |
| Subpath | `plotwerk-admin` |
| Proxy URL | `https://DEINE-ECHTE-HOSTING-ADRESSE/proxy` |

Eine App-Version mit den Bereichen und der Proxy-Konfiguration veröffentlichen und die App im eigenen Shop installieren/aktualisieren. Die `example.com`-Adresse muss durch die tatsächliche Hosting-Adresse ersetzt werden. Die App-URL kann auf dieselbe echte Hosting-Adresse verweisen; der Browser-Einstieg ist ausschließlich der App-Proxy. Ein direkter Aufruf des Backend-Roots ist absichtlich kein Adminzugang. Das Backend benötigt keine OAuth-Weiterleitungs-URL, solange App und Shop dem gleichen Shopify-Unternehmen angehören und der Client-Credentials-Grant genutzt wird.

Dann zuerst im Shop als `info@plotwerk-shop.de` anmelden und `/apps/plotwerk-admin` öffnen. Im zweiten Schritt das eigene Admin-Passwort eingeben. Neue Shopify-Kundenkonten müssen den angemeldeten Kunden im Storefront-App-Proxy bereitstellen. Liefert Shopify dort keine `logged_in_customer_id`, bleibt der Zugang gesperrt; dann muss vor dem Live-Einsatz der Kundenkonto-Authentifizierungsablauf separat integriert werden. Kein unsicherer Fallback ist eingebaut.

Optional im Theme innerhalb der bestehenden Navigation einfügen:

```liquid
{% if customer and customer.email == 'info@plotwerk-shop.de' %}
  <a href="/apps/plotwerk-admin">Plotwerk Admin</a>
{% endif %}
```

Dies steuert nur die Sichtbarkeit des Links. Der echte Zugriffsschutz wird bei **jeder Anfrage im Backend** durch Shop-Signatur, festgelegte Kunden-ID, aktuelle Kunden-E-Mail und eine nach Passwortprüfung ausgestellte Session durchgesetzt.

## 5. Prüfen, bevor du echte Änderungen vornimmst

```bash
python3 -m unittest discover -s tests -v
```

Automatisierte Tests verwenden ausschließlich eine simulierte Shopify-Anbindung. Ein Live-Test benötigt die installierte App und den eingerichteten Hoster:

1. Ausgeloggt und als anderes Kundenkonto muss der Proxy den Zugang verweigern.
2. Dein Konto ohne zusätzliches Admin-Passwort darf keine Shopdaten abrufen.
3. Einen Testartikel bearbeiten, neu laden und die Änderung in Shopify prüfen.
4. Preis und Bestand eines Testartikels ändern. Bei zwischenzeitlich geändertem Bestand muss Shopify einen Fehler melden.
5. Eine Testbestellnotiz ändern und in Shopify kontrollieren.
6. Abmelden; danach dürfen bisherige Session-Anfragen nicht mehr funktionieren.

## Sicherheit und Betrieb

Shopify-Proxy-Signaturen werden zeitlich begrenzt geprüft. Eine falsche oder fehlende Signatur, ein falscher Shop, eine fremde Kunden-ID oder fehlende Einstellungen führen zur Sperre. Sessions sind zufällig, serverseitig nur gehasht gespeichert, an die Kunden-ID gebunden und nach 30 Minuten ungültig. Fünf Passwortversuche sperren die Anmeldung für 15 Minuten. Eine neue Anmeldung widerruft bisherige Sessions. Der Browser hält die Session ausschließlich im Arbeitsspeicher, nicht in URLs, Cookies oder Local Storage. Reload erfordert eine erneute Admin-Anmeldung.

Produkt- und Bestelldaten werden im Browser mit DOM-Textfeldern dargestellt, nicht als ungeprüftes HTML. Änderungsanfragen verwenden JSON und feste GraphQL-Abfragen mit Variablen. Keine beliebigen GraphQL-Abfragen aus dem Browser. Bei Fehlern werden keine Rohantworten mit Zugangsdaten ausgegeben. Schreibzugriffe werden nicht automatisch wiederholt. Shopify-Bestandsänderungen verwenden Vergleichsbestand und Idempotenzkennung.

Das persistente Laufwerk und Backups schützen. Shopify-App-Berechtigungen beschränken und unbenutzte später entfernen. Das Änderungsprotokoll enthält Zeit, Kunden-ID, Aktion und Ressourcen-ID, keine Bestellnotizen. Dies ist eine erste implementierte Version; eine erfolgreiche Verbindung zum echten Shop wurde noch nicht getestet.

Dokumentation: [App-Proxy-Authentifizierung](https://shopify.dev/docs/apps/build/online-store/app-proxies/authenticate-app-proxies), [Client Credentials](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant), [Bestände](https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventorySetQuantities), [Render Python Hosting](https://render.com/docs/deploy-flask), [Render Disks](https://render.com/docs/disks).
