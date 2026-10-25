#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"

if [[ -z "${PERISHIFT_TEST_DATABASE_URL:-}" ]]; then
  echo "PERISHIFT_TEST_DATABASE_URL must point to a disposable PostgreSQL database." >&2
  exit 2
fi

export DATABASE_URL="${DATABASE_URL:-$PERISHIFT_TEST_DATABASE_URL}"
python -m pip install -e "$repo_root/backend[dev]"

(
  cd "$repo_root/backend"
  python -m alembic upgrade head
  python -m pytest -q
)

"$repo_root/scripts/check_contracts.sh"

(
  cd "$repo_root/web"
  npm ci
  npm run build
)
