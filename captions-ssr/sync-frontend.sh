#!/usr/bin/env sh
# Copy the two frontend caption sources into this service's frontend-mirror
# so the Remotion composition reuses the EXACT same preset table and
# per-token animation logic as the editor preview. This keeps a single
# source of truth: the originals live in frontend/companion-app/src and are
# copied here (never edited in place). The Dockerfile runs the same copy at
# image-build time; this script is for local typecheck / smoke runs.
#
# Override FRONTEND_SRC to point at the frontend src dir if the layout moves.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_SRC="${FRONTEND_SRC:-$HERE/../frontend/companion-app/src}"
MIRROR="$HERE/src/frontend-mirror"

PRESETS_SRC="$FRONTEND_SRC/lib/captionPresets.ts"
PAGE_SRC="$FRONTEND_SRC/components/cast-builder/editor-starter/items/captions/caption-page.tsx"

if [ ! -f "$PRESETS_SRC" ]; then
	echo "captionPresets.ts not found at $PRESETS_SRC" >&2
	exit 1
fi
if [ ! -f "$PAGE_SRC" ]; then
	echo "caption-page.tsx not found at $PAGE_SRC" >&2
	exit 1
fi

mkdir -p "$MIRROR/items/captions"
cp "$PRESETS_SRC" "$MIRROR/captionPresets.ts"
cp "$PAGE_SRC" "$MIRROR/items/captions/caption-page.tsx"

echo "Synced captionPresets.ts and caption-page.tsx into $MIRROR"
