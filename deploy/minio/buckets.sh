#!/bin/sh
# Legt die Buckets aus Architektur 8.1 an; wiederholbar.
set -eu
benutzer=$(cat /run/secrets/minio_benutzer)
passwort=$(cat /run/secrets/minio_passwort)
# Zugang ueber Umgebung statt Argumente
export MC_HOST_lokal="http://${benutzer}:${passwort}@minio:9000"

for bucket in laufdaten ergebnisse wissen-quellen backups; do
  mc mb --ignore-existing "lokal/${bucket}"
done
mc ilm import lokal/laufdaten < /init/lifecycle-laufdaten.json
mc ilm import lokal/ergebnisse < /init/lifecycle-ergebnisse.json
mc version enable lokal/wissen-quellen
