#!/usr/bin/env bash
# Regenerates the app icons in jobfinder/static/icons/ ("JF" in white Geist Pixel on black),
# by rendering a tiny HTML page with headless Chrome. Rerun it after changing the look.
#   scripts/make_icons.sh
#   CHROME=/path/to/chrome scripts/make_icons.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FONT="$ROOT/jobfinder/static/fonts/GeistPixel-Square.woff2"
OUT="$ROOT/jobfinder/static/icons"

# Chrome: $CHROME, then a Chrome/Chromium binary on PATH, then the macOS app.
find_chrome() {
  if [ -n "${CHROME:-}" ]; then
    [ -x "$CHROME" ] || { echo "CHROME is set but not executable: $CHROME" >&2; return 1; }
    return 0
  fi
  local candidate
  for candidate in google-chrome google-chrome-stable chromium chromium-browser; do
    if CHROME="$(command -v "$candidate" 2>/dev/null)"; then return 0; fi
  done
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  [ -x "$CHROME" ] && return 0
  echo "Chrome or Chromium not found. Install one, or set CHROME=/path/to/chrome" >&2
  return 1
}
find_chrome || exit 1
[ -f "$FONT" ] || { echo "Font not found: $FONT" >&2; exit 1; }
mkdir -p "$OUT"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# name  side  text size as a share of the side. The maskable icon keeps the letters inside the
# central safe zone (a 40% radius circle), since the OS may crop the rest.
ICONS="favicon-32 32 0.62
apple-touch-icon 180 0.46
icon-192 192 0.46
icon-512 512 0.46
icon-512-maskable 512 0.34"

while read -r name side share; do
  size="$(LC_ALL=C awk -v s="$side" -v k="$share" 'BEGIN { printf "%.2f", s * k }')"
  cat > "$WORK/$name.html" <<HTML
<!doctype html>
<meta charset="utf-8">
<style>
  @font-face { font-family: "Geist Pixel"; src: url("file://$FONT") format("woff2"); }
  html, body { margin: 0; width: ${side}px; height: ${side}px; background: #000; overflow: hidden; }
  body { display: flex; align-items: center; justify-content: center; }
  span { font: 400 ${size}px/1 "Geist Pixel", monospace; color: #fff; letter-spacing: 0.02em; }
</style>
<span>JF</span>
HTML
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --default-background-color=000000ff --virtual-time-budget=3000 \
    --window-size="$side,$side" --screenshot="$OUT/$name.png" "file://$WORK/$name.html" >/dev/null 2>&1
  echo "$OUT/$name.png (${side}x${side})"
done <<< "$ICONS"
