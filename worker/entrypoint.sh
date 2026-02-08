#!/bin/sh
set -eu

WORKER_NAME_FILE="${WORKER_NAME_FILE:-/app/data/worker/worker_name}"
WORKER_NAME_PREFIX="${WORKER_NAME_PREFIX:-celery@worker}"

mkdir -p "$(dirname "$WORKER_NAME_FILE")"

resolve_seed() {
  if [ -n "${WORKER_NODE_ID:-}" ]; then
    echo "$WORKER_NODE_ID"
    return
  fi

  if [ -f /etc/machine-id ]; then
    tr -d '\n' < /etc/machine-id
    return
  fi

  hostname
}

build_worker_name() {
  seed="$(resolve_seed)"
  if command -v sha256sum >/dev/null 2>&1; then
    suffix="$(printf '%s' "$seed" | sha256sum | cut -c1-12)"
  else
    suffix="$(printf '%s' "$seed" | md5sum | cut -c1-12)"
  fi

  echo "${WORKER_NAME_PREFIX}-${suffix}"
}

if [ -n "${WORKER_NAME:-}" ]; then
  FINAL_WORKER_NAME="$WORKER_NAME"
elif [ -f "$WORKER_NAME_FILE" ]; then
  FINAL_WORKER_NAME="$(tr -d '\r\n' < "$WORKER_NAME_FILE")"
else
  FINAL_WORKER_NAME="$(build_worker_name)"
  printf '%s\n' "$FINAL_WORKER_NAME" > "$WORKER_NAME_FILE"
fi

if [ -z "$FINAL_WORKER_NAME" ]; then
  echo "worker name is empty" >&2
  exit 1
fi

export WORKER_NAME="$FINAL_WORKER_NAME"

echo "Starting Celery worker with name: $FINAL_WORKER_NAME"
exec celery -A worker.celery_app worker --loglevel=info -n "$FINAL_WORKER_NAME"
