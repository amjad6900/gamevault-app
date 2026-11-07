#!/bin/sh
set -eu

mkdir -p /var/data/data /var/data/library
chown gamevault:gamevault /var/data /var/data/data /var/data/library
exec gosu gamevault:gamevault python /app/serveur_local.py
