#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for tool in python3 docker git; do
  if ! command -v "$tool" >/dev/null; then
    echo "Prérequis absent : $tool. Installer python3, docker et git sur le serveur." >&2
    exit 1
  fi
done
if ! docker info >/dev/null 2>&1; then
  echo "Docker inaccessible. Exécuter avec un compte autorisé à utiliser Docker." >&2
  exit 1
fi
exec python3 migration.py "$@"
