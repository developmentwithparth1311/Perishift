#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"

(
  cd "$repo_root/backend"
  python -m app.contracts.export_openapi
)

git -C "$repo_root" diff --exit-code -- backend/openapi.json
