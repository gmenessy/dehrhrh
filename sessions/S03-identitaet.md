# S03 – Identität und Zugriff

**Ziel:** Jede Anfrage an einen Plattformdienst wird über ein OIDC-JWT authentifiziert; Mandant und Scopes stammen nur aus dem Token; Token Exchange für die handelnde Person funktioniert; Quoten begrenzen Last je Mandant.

**Voraussetzungen:** S02

**Kontext lesen:** Architektur 9.1 (Scopes), 10 · Implementierung 4 (`kern.auth`), 8

## Aufgaben

1. `kern.auth.pruefung`:
   - JWKS laden von `OIDC_JWKS_URL`, Cache mit TTL 10 min; unbekannte `kid` lösen höchstens einen Nachladeversuch je 60 s aus.
   - Erlaubte Algorithmen: `RS256`, `PS256`, `ES256`, `EdDSA`. `none` und alle `HS*` werden abgelehnt.
   - Prüfen: `iss` = `OIDC_ISSUER`, `aud` enthält `OIDC_AUDIENCE`, `exp`, `nbf`, `iat`; Lebensdauer `exp - iat` ≤ 300 s; Uhrabweichung 30 s.
   - Claims: `sub`, `mandant` (Pflicht, Muster `^[a-z0-9-]{2,40}$`), `scope` (Leerzeichen-getrennt), optional `act`.
   - Ergebnis `Identitaet(sub, mandant, scopes: frozenset[str], act: dict | None, token: str)` – das Rohtoken nur für Token Exchange, nie loggen.
2. `kern.auth.fastapi`: Abhängigkeit `braucht(*scopes)` setzt `request.state.identitaet` und Log-Kontext `mandant`; Fehler `NICHT_ANGEMELDET` (401 mit `WWW-Authenticate: Bearer`) oder `KEIN_RECHT` (403).
3. `kern.auth.pruefe_routen(app, ausgenommen={"/health", "/ready", "/metrics"})`: Testhilfe, die meldet, wenn eine Route ohne `braucht` registriert ist.
4. `kern.auth.austausch`: `tausche(subject_token, *, audience, scope) -> str` nach RFC 8693 gegen `OIDC_TOKEN_URL` mit Client-Credentials des Dienstes (`OIDC_CLIENT_SECRET_DATEI`); `act` wird aus dem Ergebnis gelesen und zurückgegeben. `dienst_token(scope)` holt Client-Credentials-Tokens mit Cache bis 30 s vor Ablauf.
5. `kern.quote`: Token-Bucket je `(mandant, sub)` und je `mandant`, Grenzen aus Konfiguration; Überschreitung → `KONTINGENT` mit `Retry-After`. Im Speicher (ein Runner-Prozess im MVP; Grenze dokumentieren).
6. `kern.auth.testing`: Test-Aussteller mit eigenem Schlüsselpaar, `token(**claims)`, JWKS über `httpx.MockTransport`; von allen Diensten in Tests nutzbar.
7. Keycloak im Dev-Profil (`deploy/compose.dev.yaml`, Netz `idp`, Port 8443) mit `deploy/keycloak/realm-dev.json`:
   - Realm `plattform`, Token-Lebensdauer 300 s, Audience-Mapper `plattform`, Claim `mandant` aus Benutzer- bzw. Client-Attribut;
   - Clients: `fachverfahren-dev` (Client Credentials, Mandant `musterhausen`, Scopes `uc.lesen uc.ausfuehren`), `runner` (Client Credentials, Standard Token Exchange erlaubt, Scopes `tm.* modell.nutzen wissen.lesen`), `freigabeliste` (öffentlich, Authorization Code + PKCE), `zweiter-mandant-dev` (Mandant `beispielstadt`);
   - Benutzer `sachbearbeitung-dev` (Mandant `musterhausen`, Scope `uc.freigeben`).
   Scopes als Client Scopes anlegen: `uc.lesen`, `uc.ausfuehren`, `uc.freigeben`, `uc.admin`, `modell.nutzen`, `wissen.lesen`, `wissen.admin`, `tm.zuordnung` sowie je Werkzeug `tm.<werkzeug>` (Liste aus `vorhandenes/taschenmesser/app/main.py`).

## Akzeptanzkriterien

- [ ] Negativtests, je einer: abgelaufen, `nbf` in der Zukunft, falsches `aud`, falsches `iss`, `alg: none`, `HS256` mit dem öffentlichen Schlüssel als Geheimnis, fehlender `mandant`, ungültiges Mandantenformat, Lebensdauer 600 s, unbekannte `kid` (genau ein Nachladeversuch).
- [ ] Fehlender Scope → 403 `KEIN_RECHT`; fehlendes Token → 401 mit `WWW-Authenticate`.
- [ ] Quote: der 11. Aufruf bei Grenze 10/min liefert 429 mit `Retry-After`.
- [ ] `pruefe_routen` meldet eine absichtlich ungeschützte Test-Route.
- [ ] Integration (`make test-int`): Token von Keycloak für `fachverfahren-dev` wird akzeptiert und liefert `mandant = musterhausen`; Token Exchange für `sachbearbeitung-dev` durch Client `runner` liefert ein Token, dessen `act` den Runner nennt (oder, falls Keycloak `act` nicht setzt: ADR mit der gewählten Abbildung, z. B. `azp`).
- [ ] `mypy --strict` für `kern` grün, Abdeckung ≥ 85 %.

## Nicht in dieser Session

Keine Dienstrouten außer Testrouten; Freigabelogik (S16).

## Prüfung

```
make check && make up && make test-int
```
