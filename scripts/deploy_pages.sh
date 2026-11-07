#!/usr/bin/env bash
# Publish landing + a frozen snapshot of the floor to GitHub Pages (gh-pages branch).
# Needs the floor running on :8766. Re-run any time to refresh the snapshot.
#   SWITCH_LINK=<demo video url> scripts/deploy_pages.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd); REPO=quant-swarms-switch-hackathon
OUT=$(mktemp -d); trap 'rm -rf "$OUT"' EXIT
python3 "$ROOT/scripts/snapshot_floor.py" "$OUT/floor"
(cd "$ROOT/landing" && VITE_SWITCH_URL="${SWITCH_LINK:-https://github.com/AtharvaMaskar/$REPO#readme}" VITE_FLOOR_URL="./floor/" \
  npx vite build --base="/$REPO/" --outDir "$OUT/landing" --emptyOutDir >/dev/null)
cp -R "$OUT/landing/." "$OUT/floor/.." && rm -rf "$OUT/landing" && touch "$OUT/.nojekyll"
cd "$OUT" && git init -q -b gh-pages && git add -A && git commit -qm "deploy: landing + floor snapshot" \
  && git push -qf "$(git -C "$ROOT" remote get-url origin)" gh-pages
echo "https://atharvamaskar.github.io/$REPO/"
