#!/bin/sh
# Puts the shipped master catalog into the data volume on first start, then
# runs the app. Files already there (edited by a CSV import) are never replaced.
set -eu

seed=/app/app/data
dest="${DATA_DIR:-$seed}"
if [ "$dest" != "$seed" ]; then
    mkdir -p "$dest"
    cp -rn "$seed"/. "$dest"/
fi

exec "$@"
