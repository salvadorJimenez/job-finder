#!/usr/bin/env bash
# Regenerates the README screenshots in docs/screenshots/ from fictional demo data.
#   scripts/screenshots.sh
#   PORT=8791 CHROME=/path/to/chrome scripts/screenshots.sh
#
# Everything happens in a throwaway folder: a config and a database seeded by
# scripts/demo_data.py, a server on 127.0.0.1:$PORT (default 8790, never 8000) and a Chrome
# profile. Your own config (config/ or config.toml), data/ and the server on port 8000 are never touched. The
# folder and the server are removed when the script ends. Needs Chrome or Chromium.
# Optional: SCALE (device pixel ratio, default 1.5), WIDTH (default 1440).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/screenshots"
PORT="${PORT:-8790}"
SCALE="${SCALE:-1.5}"
WIDTH="${WIDTH:-1440}"
HOST=127.0.0.1

[ "$PORT" != 8000 ] || { echo "Port 8000 is yours (the app in Docker or serve); pick another PORT." >&2; exit 1; }

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

# The project's virtualenv when there is one, else whatever is on PATH.
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"; JOB_FINDER="$ROOT/.venv/bin/job-finder"
else
  PYTHON="$(command -v python3)"; JOB_FINDER="$(command -v job-finder || true)"
fi
[ -n "$JOB_FINDER" ] && [ -x "$JOB_FINDER" ] || { echo "job-finder is not installed: pip install -e . (see README)" >&2; exit 1; }

# Never reuse a port that already answers: that would be somebody else's server.
if curl -s -o /dev/null --max-time 2 "http://$HOST:$PORT/"; then
  echo "Something already answers on $HOST:$PORT. Pick another PORT." >&2
  exit 1
fi

WORK="$(mktemp -d)"
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# A config of its own, derived from the example: demo stack and goal, storage inside $WORK.
sed -e "s|^stack = .*|stack = [\"TypeScript\", \"React\", \"Python\", \"PostgreSQL\", \"Node.js\"]|" \
    -e "s|^daily = .*|daily = 2|" \
    -e "s|^database = \"[^\"]*\"|database = \"$WORK/data/jobs.db\"|" \
    -e "s|^exports_dir = \"[^\"]*\"|exports_dir = \"$WORK/exports\"|" \
    "$ROOT/config.example.toml" > "$WORK/config.toml"
grep -q "$WORK/data/jobs.db" "$WORK/config.toml" || { echo "Could not point the demo config at $WORK" >&2; exit 1; }

SEEDED="$("$PYTHON" "$ROOT/scripts/demo_data.py" --config "$WORK/config.toml")"
JOB_ID="$(printf '%s\n' "$SEEDED" | sed -n 's/^featured_job=//p')"
APP_ID="$(printf '%s\n' "$SEEDED" | sed -n 's/^featured_application=//p')"
[ -n "$JOB_ID" ] && [ -n "$APP_ID" ] || { echo "demo_data.py did not report the pages to capture" >&2; exit 1; }

"$JOB_FINDER" --config "$WORK/config.toml" serve --host "$HOST" --port "$PORT" > "$WORK/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  curl -s -o /dev/null --max-time 2 "http://$HOST:$PORT/" && break
  kill -0 "$SERVER_PID" 2>/dev/null || { cat "$WORK/server.log" >&2; echo "The demo server stopped" >&2; exit 1; }
  sleep 0.5
done
curl -s -o /dev/null --max-time 2 "http://$HOST:$PORT/" || { cat "$WORK/server.log" >&2; echo "The demo server did not answer" >&2; exit 1; }

mkdir -p "$OUT"

# Chrome's own --screenshot flag paints a page that scrolls on load (the jobs list brings its pane
# into view) with a black band on top, so the capture talks to Chrome over its DevTools pipe instead,
# using only the Python standard library. One Chrome serves the three pages; it waits for the
# fonts and for the page to settle before it takes each picture.
cat > "$WORK/capture.py" <<'PY'
import base64, json, os, subprocess, sys

chrome, profile, scale, width, base, out = sys.argv[1:7]
shots = [a.split("@") for a in sys.argv[7:]]            # file@path@height

to_chrome_r, to_chrome_w = os.pipe()
from_chrome_r, from_chrome_w = os.pipe()


def wire():
    os.dup2(to_chrome_r, 3)
    os.dup2(from_chrome_w, 4)


proc = subprocess.Popen(
    [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
     "--no-default-browser-check", f"--user-data-dir={profile}", "--remote-debugging-pipe", "about:blank"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(3, 4), preexec_fn=wire,
)
os.close(to_chrome_r)
os.close(from_chrome_w)
writer, reader = os.fdopen(to_chrome_w, "wb"), os.fdopen(from_chrome_r, "rb")
counter = 0


def read_message():
    buffer = bytearray()
    while True:
        byte = reader.read(1)
        if not byte:
            raise SystemExit("Chrome closed the DevTools pipe")
        if byte == b"\0":
            return json.loads(buffer)
        buffer += byte


def call(method, params=None, session=None):
    global counter
    counter += 1
    message = {"id": counter, "method": method, "params": params or {}}
    if session:
        message["sessionId"] = session
    writer.write(json.dumps(message).encode() + b"\0")
    writer.flush()
    while True:
        reply = read_message()
        if reply.get("id") == counter:
            if "error" in reply:
                raise SystemExit(f"{method}: {reply['error']}")
            return reply["result"]


def wait_for(event, session):
    while True:
        message = read_message()
        if message.get("method") == event and message.get("sessionId") == session:
            return


try:
    target = call("Target.createTarget", {"url": "about:blank"})["targetId"]
    session = call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
    call("Page.enable", session=session)
    for name, path, height in shots:
        call("Emulation.setDeviceMetricsOverride", {"width": int(width), "height": int(height),
             "deviceScaleFactor": float(scale), "mobile": False}, session)
        call("Page.navigate", {"url": base + path}, session)
        wait_for("Page.loadEventFired", session)
        call("Runtime.evaluate", {"awaitPromise": True, "expression":
             "document.fonts.ready.then(() => new Promise(done => setTimeout(done, 700)))"}, session)
        data = call("Page.captureScreenshot", {"format": "png"}, session)["data"]
        with open(os.path.join(out, name), "wb") as fh:
            fh.write(base64.b64decode(data))
finally:
    proc.terminate()
    proc.wait()
PY
"$PYTHON" "$WORK/capture.py" "$CHROME" "$WORK/chrome" "$SCALE" "$WIDTH" "http://$HOST:$PORT" "$OUT" \
  "jobs.png@/?job=$JOB_ID@${JOBS_HEIGHT:-900}" \
  "board.png@/applications@${BOARD_HEIGHT:-1000}" \
  "application.png@/applications/$APP_ID@${APPLICATION_HEIGHT:-1560}" \
  || { echo "Capturing with Chrome failed" >&2; exit 1; }
for file in jobs.png board.png application.png; do
  [ -s "$OUT/$file" ] || { echo "No screenshot written: $file" >&2; exit 1; }
done

# Smaller files when an optimizer is installed (nothing is installed for you).
for tool in oxipng optipng; do
  if command -v "$tool" > /dev/null 2>&1; then
    case "$tool" in
      oxipng) oxipng -q --strip safe "$OUT"/jobs.png "$OUT"/board.png "$OUT"/application.png ;;
      optipng) optipng -quiet "$OUT"/jobs.png "$OUT"/board.png "$OUT"/application.png ;;
    esac
    break
  fi
done

ls -l "$OUT"/jobs.png "$OUT"/board.png "$OUT"/application.png
