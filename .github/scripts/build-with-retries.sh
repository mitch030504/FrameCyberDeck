#!/usr/bin/env bash
set -euo pipefail
: "${BUILD_COMMAND:?BUILD_COMMAND is required}"
for attempt in 1 2 3; do
  if bash -e -o pipefail -c "$BUILD_COMMAND"; then
    exit 0
  fi
  if [ "$attempt" -lt 3 ]; then
    echo "Build attempt $attempt failed, retrying in 30s..."
    sleep 30
  fi
done
echo "::error::Build failed after three attempts."
exit 1
