#!/usr/bin/env bash
# Erzeugt Dev-Geheimnisse als Dateien (nie als Variablen). Bestehende Dateien bleiben unveraendert.
set -euo pipefail

ziel="${1:?Zielverzeichnis fehlt}"
mkdir -p "$ziel"
chmod 700 "$ziel"

neu() {
  local datei="$ziel/$1"
  if [[ ! -s "$datei" ]]; then
    printf '%s' "$2" > "$datei"
  fi
}

zufall() { python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")'; }

neu postgres.dev "$(zufall)"
neu minio-benutzer.dev "plattform-dev"
neu minio-passwort.dev "$(zufall)"
# Container laufen als Benutzer 10001 und lesen die Dateien ueber Compose-Secrets
chmod 644 "$ziel"/postgres.dev "$ziel"/minio-benutzer.dev "$ziel"/minio-passwort.dev
