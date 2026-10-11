# Plotwerk Admin

Ein eigener Adminbereich unter `/apps/plotwerk-admin` im Shopify-Shop, mit dunklem Plotwerk-Design und pink-violetten Akzenten. Der Code ist eine erste Version, kein bereits eingerichteter oder live getesteter Dienst.

## Neu: Übersicht, Kategorien, Suche und Export

- Shop-Übersicht mit aktiven Produkten, Entwürfen und offenen Bestellungen. Beschriftete Stichprobe: höchstens 100 Produkte und 50 neueste Bestellungen, keine vollständigen Analytics oder Umsatzstatistik.
- Kategorienübersicht aus Shopify-Kollektionen: Titel, Produktanzahl und Link zur Kollektion. Keine Änderung der Kollektionen in dieser Version.
- Suche in den jeweils geladenen Einträgen, einschließlich SKU und Bestellnotizen. Weitere Einträge zuerst nachladen.
- Bestell-CSV für die geladenen, gefilterten Bestellungen. CSV-Felder sind escaped und gegen typische Tabellenformeln geschützt.

## Enthalten

- Bestehende Produkte bearbeiten: Titel, Marke, Produkttyp und Status.
- Preise je Variante direkt speichern.
- Verfügbaren Bestand je Variante und Standort ändern. Shopify prüft den vorherigen Bestand; Änderungen aus einer zwischenzeitlichen Bestellung werden nicht still überschrieben.
- Bestellungen der letzten 60 Tage ansehen und deren Notizen bearbeiten.
- Seitennavigation und Nachladen von jeweils 5 Produkten oder 25 Bestellungen.
- Zugriff nur für die fest konfigurierte Shopify-Kunden-ID und deren E-Mail `info@plotwerk-shop.de`, zusätzlich mit einem eigenen Admin-Passwort.

Noch nicht enthalten: neue Produkte erstellen, Produktbilder hochladen, Kollektionen verwalten, Rabatte, Versandetiketten, Erstattungen, Design-Editor oder vollständige Shopify-Verwaltung. Produktstatus „Aktiv“ allein veröffentlicht ein Produkt nicht automatisch in neuen Vertriebskanälen. Pro Produkt werden bis zu 10 Varianten und pro Variante bis zu 5 Standorte gezeigt; weitere Daten werden als solche kenntlich gemacht. Bestellstatus werden in dieser Version mit den Shopify-Statusbezeichnungen angezeigt.

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

Das Passwort zweimal verdeckt eingeben. Den ausgegebenen Hash als `ADMIN_PASSWORD_HASH` beim Hoster speichern. Das Passwort selbst wird nicht gespeichert. Nach einem Passwortwechsel den Dienst neu starten und alle Sessions durch `DELETE FROM sessions` in der Datenbank widerrufen; diese Version hat noch keine Passwortwechsel-Oberfläche.

## 3. Kostenlos betreiben: Render Free + Neon Free

Für diese Einrichtung ist kein kostenpflichtiges Webhosting und kein zusätzliches Domain-Abo nötig. Render führt die App aus; Neon speichert Sessions, Passwortversuche und Änderungsprotokoll dauerhaft in PostgreSQL. Produkt- und Bestelldaten bleiben in Shopify. Kostenlose Tarife gelten innerhalb der aktuellen Anbieterlimits; sie garantieren weder dauerhaft unveränderte Preise noch Verfügbarkeit.

### A. Neon-Datenbank anlegen

1. Auf [Neon](https://console.neon.tech) anmelden, **Free** wählen und ein Projekt `plotwerk-admin` erstellen. Wenn angeboten eine EU-Region wählen.
2. **Connect** öffnen und die PostgreSQL-Verbindungsadresse kopieren (Connection String, vorzugsweise Pooled). Die Adresse enthält ein Datenbankpasswort: ausschließlich in Render als `DATABASE_URL` hinterlegen, niemals im Repository oder im Chat veröffentlichen.
3. Scale to zero aktiv lassen; nur diese kleine Admin-Datenbank verwenden. Die App legt ihre Tabellen automatisch an. Bei Datenbankfehlern gibt es keinen Ersatz durch flüchtigen Speicher.

### B. Render einrichten

1. Bei [Render](https://dashboard.render.com) mit GitHub anmelden.
2. **New → Blueprint**, dieses Repository verbinden. Die mitgelieferte `render.yaml` wählt ausdrücklich **plan: free**, ohne kostenpflichtige Datenbank und ohne Laufwerk.
3. Die abgefragten Secrets aus der Tabelle unten eintragen. `DATABASE_URL` kommt von Neon. `REQUIRE_POSTGRES=true` ist bereits in der Vorlage gesetzt und verhindert einen versehentlichen Start mit flüchtiger SQLite-Datenbank.
4. Vor Erstellung prüfen, dass der Webdienst **Free** ist und keine kostenpflichtige Ressource aufgeführt wird. Keine kostenpflichtige Option aktivieren. Aktuelle Gratislimits und Ausgabenbegrenzung im Konto prüfen; enthaltenes Kontingent nicht absichtlich überschreiten.
5. Nach erfolgreichem Deployment die echte `https://…onrender.com`-Adresse notieren und damit den Shopify-App-Proxy in Schritt 4 verbinden.

Falls du stattdessen **New → Web Service** verwendest, Python und ausdrücklich **Free** auswählen:

| Einstellung | Wert |
| --- | --- |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60 app:application` |
| Health Check | `/healthz` |
| `DATABASE_URL` | Geheime Neon-Verbindungsadresse |
| `REQUIRE_POSTGRES` | `true` |
| Laufwerk / Render-Datenbank | Keines anlegen |

Render Free pausiert nach 15 Minuten ohne Aufrufe. Beim nächsten Öffnen kann der Start etwa eine Minute dauern, eventuell muss die Shop-Seite danach neu geladen werden. SQLite-Dateien würden dabei verloren gehen; deshalb nutzt diese Einrichtung Neon. Auch Neon kann bei Inaktivität pausieren, behält dabei aber die Daten. Kein künstliches Dauer-Pinging verwenden. Wenn ein Gratislimit erreicht wird, kann der Adminbereich vorübergehend nicht verfügbar sein; das Shopify-Admin bleibt als Zugang erhalten.

Für lokale Entwicklung ist SQLite weiterhin möglich: `DATABASE_URL` weglassen, `REQUIRE_POSTGRES` nicht auf `true` setzen und `DATABASE_PATH=data/admin.sqlite3` verwenden. Für bereits vorhandenes eigenes Docker-Hosting kann `/data` weiterhin als dauerhaftes Volume eingebunden werden. Die Gratisvorlage benötigt kein solches Volume.

Diese Umgebungsvariablen **als Secret-Einstellungen** setzen:

| Name | Inhalt |
| --- | --- |
| `SHOPIFY_SHOP` | Echte Shop-Adresse, z. B. `dein-shop.myshopify.com`, ohne `https://` |
| `SHOPIFY_CLIENT_ID` | Client-ID deiner eigenen App |
| `SHOPIFY_CLIENT_SECRET` | Client-Secret deiner eigenen App |
| `ADMIN_CUSTOMER_ID` | Numerische ID deines Kundenkontos |
| `ADMIN_PASSWORD_HASH` | Ausgabe des Passwort-Tools |
| `APP_PROXY_PATH` | `/apps/plotwerk-admin` |
| `DATABASE_URL` | PostgreSQL-Verbindungsadresse aus Neon, als Secret |
| `REQUIRE_POSTGRES` | `true` für den kostenlosen Betrieb |

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

18 automatisierte Tests prüfen Zugriffsschutz, Sessions und Datenbankgrenzen. Shopify und die PostgreSQL-Verbindung werden dabei simuliert; die Sessionlogik läuft zusätzlich mit lokaler SQLite. Eine echte Neon-Verbindung inklusive TLS und SQL muss nach der Einrichtung geprüft werden. Ein Live-Test benötigt die installierte App und den eingerichteten Hoster:

1. Ausgeloggt und als anderes Kundenkonto muss der Proxy den Zugang verweigern.
2. Dein Konto ohne zusätzliches Admin-Passwort darf keine Shopdaten abrufen.
3. Einen Testartikel bearbeiten, neu laden und die Änderung in Shopify prüfen.
4. Preis und Bestand eines Testartikels ändern. Bei zwischenzeitlich geändertem Bestand muss Shopify einen Fehler melden.
5. Eine Testbestellnotiz ändern und in Shopify kontrollieren.
6. Abmelden; danach dürfen bisherige Session-Anfragen nicht mehr funktionieren.

## Sicherheit und Betrieb

Shopify-Proxy-Signaturen werden zeitlich begrenzt geprüft. Eine falsche oder fehlende Signatur, ein falscher Shop, eine fremde Kunden-ID oder fehlende Einstellungen führen zur Sperre. Sessions sind zufällig, serverseitig nur gehasht gespeichert, an die Kunden-ID gebunden und nach 30 Minuten ungültig. Fünf Passwortversuche sperren die Anmeldung für 15 Minuten. Eine neue Anmeldung widerruft bisherige Sessions. Der Browser hält die Session ausschließlich im Arbeitsspeicher, nicht in URLs, Cookies oder Local Storage. Reload erfordert eine erneute Admin-Anmeldung.

Produkt- und Bestelldaten werden im Browser mit DOM-Textfeldern dargestellt, nicht als ungeprüftes HTML. Änderungsanfragen verwenden JSON und feste GraphQL-Abfragen mit Variablen. Keine beliebigen GraphQL-Abfragen aus dem Browser. Bei Fehlern werden keine Rohantworten mit Zugangsdaten ausgegeben. Schreibzugriffe werden nicht automatisch wiederholt. Shopify-Bestandsänderungen verwenden Vergleichsbestand und Idempotenzkennung.

Den Datenbankzugang schützen; Wiederherstellungs- und Exportmöglichkeiten des gewählten Datenbanktarifs prüfen. Shopify-App-Berechtigungen beschränken und unbenutzte später entfernen. Das Änderungsprotokoll enthält Zeit, Kunden-ID, Aktion und Ressourcen-ID, keine Bestellnotizen. Dies ist eine erste implementierte Version; eine erfolgreiche Verbindung zum echten Shop wurde noch nicht getestet.

Dokumentation: [App-Proxy-Authentifizierung](https://shopify.dev/docs/apps/build/online-store/app-proxies/authenticate-app-proxies), [Client Credentials](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant), [Bestände](https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventorySetQuantities), [Render Python Hosting](https://render.com/docs/deploy-flask), [Render Free](https://render.com/docs/free), [Neon Free](https://neon.com/blog/neon-free-plan-1-gb-per-project), [Neon-Verbindung](https://neon.com/docs/get-started/connect-neon).

## Admin-Link direkt neben Konto im Shopify-Header

Dieser Schritt muss im Shopify-Theme durchgeführt werden. Das Backend-Repository enthält keinen Zugriff auf das live verwendete Theme und hat es nicht verändert. Die Domain plotwerk-shop.de führt derzeit noch zu Base44; im neuen Shopify-Shop wird der relative Link bereits unter seiner myshopify.com-Domain verwendet.

1. Shopify → Onlineshop → Themes → beim gewünschten Theme → Code bearbeiten.
2. Unter Snippets die Datei `plotwerk-admin-link.liquid` anlegen und den Inhalt aus `theme/snippets/plotwerk-admin-link.liquid` dieses Repositorys einfügen.
3. Die Datei öffnen, die die Konto-Aktion im Header rendert (bei Heritage häufig `snippets/header-actions.liquid`; anhand des tatsächlichen Themes prüfen).
4. Direkt vor oder nach dem Konto-Element, innerhalb der gemeinsamen Header-Aktionsgruppe, aber **außerhalb** bestehender Links/Buttons einfügen:

```liquid
{% render 'plotwerk-admin-link' %}
```

5. Eingeloggt als info@plotwerk-shop.de auf Desktop und Mobil prüfen. Als fremder Kunde und ausgeloggt darf der Link nicht sichtbar sein.

Neue Kundenkonten können den `customer`-Status im Theme anders bereitstellen. Falls das Theme keinen angemeldeten Kunden erkennt, erscheint der Link nicht; die Prüfung keinesfalls durch eine Browser-E-Mail ersetzen. Für den exakten Einbauort wird die tatsächliche Header-Datei benötigt.

## Erweiterung: Katalog, Marketing und Lager

Neue Bereiche im eigenen Admin:

- Produkte anlegen (Entwurf) und bearbeiten: Titel, HTML-Beschreibung, URL-Kennung, Marke, Typ, Tags, Status und SEO. Produktbilder über HTTPS-Adressen hinzufügen/ersetzen, Alternativtexte bearbeiten und Zuordnungen entfernen (auch Varianten-Bildzuordnungen werden dabei entfernt); Shopify verarbeitet Bilder asynchron. Varianten werden in Seiten zu 30 geladen und können vollständig durchgeblättert werden: Preis, Vergleichspreis, SKU, Barcode, Steuerpflicht und Verhalten bei ausverkauftem Bestand.
- Kategorien anlegen und bearbeiten: Titel, Beschreibung, URL, SEO und Bild über eine HTTPS-Adresse. Produkte zu manuellen Kategorien hinzufügen/entfernen. Automatische Regeln bleiben erhalten.
- Rabattcodes („Gutscheine“) und automatische Prozent-/Betragsrabatte ab einer Mindeststückzahl, Beginn/Ende und bei Codes optional Nutzungslimit/einmal je Kunde. Gilt für alle Artikel, ohne Kombination. **Keine verkaufbaren Geschenkkarten und keine mehrstufigen Staffelpreise.** Weitere Rabattarten über den Shopify-Link.
- News-Ticker im Shop-Metafeld `plotwerk.news_ticker`, mit Prüfung paralleler Änderungen. Kein animiertes Laufband, damit der Hinweis ruhig lesbar bleibt.
- Code-128-Lageretiketten aus SKU oder vorhandenem Barcode, Vorschau und A4-Druck bis 100 Etiketten. Das vergibt keine EAN/GTIN. Lagerkorrekturen behalten Mengenvergleich und Änderungskennung.
- IMAP-Postfach abrufen, Antworten als Entwurf speichern und nach Freigabe automatisch per HTTPS-Maildienst an den ursprünglichen From-Absender versenden. Absenderangaben einer E-Mail sind kein Identitätsnachweis. Import: letzte 30 Nachrichten bis 200 KB, nur Klartext, keine Anhänge.
- Newsletter in Klartext verfassen, Vorschau freigeben, alle Shopify-Kunden seitenweise prüfen und ausschließlich `SUBSCRIBED` übernehmen. Vor jedem Versand wird die Einwilligung und aktuelle Adresse erneut geprüft. Doppelte Adressen je Kampagne werden zusammengefasst. Abmeldelink mit signierter Bestätigung; lokale Sperrliste verhindert weitere Newsletter aus dieser App. Diese Sperrliste ändert **nicht** den Shopify-Marketingstatus für andere Versandwerkzeuge.

### Grenzen der eigenen Oberfläche

Dies ist keine vollständige Kopie des Shopify-Admins. Variantenoptionen/neue Varianten, lokale Medienuploads/Videos/3D, Versanddaten, Metafelder, Veröffentlichungen, automatische Kategorieregeln sowie Geschenkkarten werden über die vorhandenen Shopify-Links gepflegt. Produkt- und Lagerlisten zeigen weiterhin die ersten 10 Varianten und 5 Standorte je Produkt; die Detailbearbeitung lädt weitere Varianten. Shopify-Anfragen wurden mit Testantworten geprüft, nicht gegen einen echten Shop mit Zugangsdaten.

### Einmalige Einrichtung

1. In der Shopify-App zusätzlich `read_discounts`, `write_discounts` und `write_files` freigeben. Bestehende Rechte für Produkte, Lager, Kunden und Bestellungen beibehalten. Eine neue App-Version veröffentlichen und erforderlichenfalls Installation/Berechtigungen aktualisieren. Für geschützte Kundendaten muss die App Zugriff auf E-Mail/Marketingzustand erhalten.
2. Ticker-Snippet `theme/snippets/plotwerk-news-ticker.liquid` als `snippets/plotwerk-news-ticker.liquid` in das aktive Shopify-Theme kopieren. In `layout/theme.liquid` nach dem Header `{% render 'plotwerk-news-ticker' %}` einfügen. Falls eine Metafelddefinition bereits existiert, muss Typ JSON und Schreibzugriff passend sein. Der Ticker erscheint erst nach diesem Einbau.
3. Der Admin-Link bleibt separat: `theme/snippets/plotwerk-admin-link.liquid` im Header neben dem Konto-Element rendern. Er erscheint nur für `info@plotwerk-shop.de`; die API verlangt zusätzlich Kundennummer, signierten Proxy und Admin-Passwort. Ohne Zugang zum Theme kann dieses Repository den Header nicht direkt ändern.
4. In **Render → Service → Environment** Mail-Einstellungen aus `.env.example` ergänzen. `RESEND_API_KEY` aus deinem Resend-Konto und `MAIL_FROM=info@plotwerk-shop.de` eintragen. In Resend die Domain als Absender verifizieren; dazu ausschließlich die von Resend vorgegebenen DNS-Einträge beim Domainanbieter ergänzen, ohne die Shop-Domain umzuleiten oder vorhandene Mail-MX-Einträge zu ersetzen. Render Free blockiert ausgehendes SMTP auf 25/465/587; deshalb wird Resend über HTTPS verwendet. Der kostenlose Resend-Tarif hat derzeit 100 Nachrichten/Tag und 3.000/Monat ([Tarife](https://resend.com/pricing)); auch Antworten verbrauchen dieses Kontingent. `MAIL_DAILY_LIMIT=100`, `MAIL_MONTHLY_LIMIT=3000` sind die Vorgaben. IMAP-Host aus den tatsächlichen Postfach-Einstellungen übernehmen; SSL auf 993. SMTP SSL ist nur als optionaler Ersatz auf anderem Hosting implementiert. `MAIL_PUBLIC_URL=https://plotwerk-admin.onrender.com` (direkte Hosting-URL, nicht die noch auf Base44 zeigende Shop-Domain). Je einen separaten zufälligen Schlüssel für `MAIL_SIGNING_KEY` und `MAIL_WORKER_KEY` erzeugen; mindestens 32 Zeichen. Nie in GitHub oder Chats eintragen. `MAIL_DAILY_LIMIT` an das tatsächliche Versandlimit des Mailanbieters anpassen.
5. Test: eigenes Postfach importieren, Antwortentwurf prüfen und an eine eigene Testanfrage senden. Newsletter zunächst mit einem eigenen abonnierten Testkonto prüfen, einschließlich Abmeldelink. Maildienst „angenommen“ ist kein Nachweis der Zustellung; Rückläufer im Postfach kontrollieren.

### Automatischer Versand und Render Free

Warteschlange und Versandstatus sind dauerhaft in Neon gespeichert. Während der E-Mail-Bereich geöffnet ist, arbeitet die Oberfläche alle 15 Sekunden weiter. Für Versand **auch bei geschlossenem Browser** muss ein externer Scheduler regelmäßig (z.B. jede Minute) `POST https://plotwerk-admin.onrender.com/mail-worker` mit Header `Authorization: Bearer <MAIL_WORKER_KEY>` aufrufen. Kein Schlüssel in der URL. Alternativ `tools/run_mail_worker.py` in einer bereits vorhandenen externen Umgebung mit den beiden Umgebungsvariablen regelmäßig starten. **Es ist noch kein Scheduler eingerichtet.** Render Free kann schlafen; externe Aufrufe können deshalb länger dauern. Regelmäßiges Aufwecken verbraucht die kostenlosen Betriebsstunden und garantiert keine kostenfreie unbegrenzte Verfügbarkeit.

Jeder Lauf sammelt höchstens 50 Kunden und sendet höchstens zwei Nachrichten, begrenzt durch `MAIL_DAILY_LIMIT` (Standard 100 pro rollenden 24 Stunden und 3.000 pro rollenden 30 Tagen). Größere Verteiler werden über mehrere Tage abgearbeitet; ein Anbieterlimit kann den Versand ablehnen. Nach Freigabe erfolgen die einzelnen Versandschritte ohne weitere Bestätigung. Kampagne abbrechen stoppt noch wartende Nachrichten; bereits laufende/angenommene Nachrichten lassen sich nicht zurückholen. Status `uncertain` wird **nie automatisch erneut gesendet**; im Mailpostfach prüfen, damit ein Timeout keine Duplikate auslöst. HTTPS-Anfragen an Resend tragen zusätzlich eine feste Idempotenzkennung je Nachricht; angenommene Provider-IDs werden gespeichert. Kontakte mit lokalem Opt-out werden übersprungen. Die Abmeldebestätigung erfolgt per POST nach Öffnen des Links, damit Link-Scanner keine automatischen Abmeldungen auslösen.
