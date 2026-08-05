#!/usr/bin/env bash
#
# Drive the pidprobe demo end to end, non-interactively, in about 15 seconds.
#
# The script exists to be recorded: it prints each command the way a shell
# would echo it, runs it, and paces itself so the result is readable at
# playback speed. It is also the fastest way to check that the walkthrough in
# docs/demo.md still does what it says.
#
# Recording it:
#
#     docker build -t pidprobe-demo -f demo/Dockerfile .
#     asciinema rec demo.cast --overwrite --cols 100 --rows 30 \
#         -c scripts/record_demo.sh
#     agg --cols 100 --rows 30 demo.cast demo.gif
#
# Needs docker, curl and jq on the host; everything else lives in the image.
# A container named $PIDPROBE_DEMO_CONTAINER is force-removed on the way in
# and on the way out, so do not point that at a container you care about.
#
set -euo pipefail

IMAGE="${PIDPROBE_DEMO_IMAGE:-pidprobe-demo}"
CONTAINER="${PIDPROBE_DEMO_CONTAINER:-pidprobe-demo}"
PORT="${PIDPROBE_DEMO_PORT:-8000}"
BASE="http://localhost:${PORT}"

BOLD=$'\033[1m'
DIM=$'\033[2m'
RESET=$'\033[0m'

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

require() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "record_demo.sh needs '$1' on PATH" >&2
        exit 1
    }
}

# Echo a command the way a shell prompt would, then run it.
run() {
    printf '%s$ %s%s\n' "$BOLD" "$*" "$RESET"
    "$@"
    echo
}

# Same, for a command line that is a pipeline and has to reach a shell whole.
# `-o pipefail` so a failing pidprobe is not hidden by a jq that succeeds on
# empty input -- this script doubles as the CI check for the demo image.
run_pipeline() {
    printf '%s$ %s%s\n' "$BOLD" "$1" "$RESET"
    bash -o pipefail -c "$1"
    echo
}

note() {
    printf '%s# %s%s\n' "$DIM" "$*" "$RESET"
}

require docker
require curl
require jq

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    echo "image '$IMAGE' not found -- build it first:" >&2
    echo "    docker build -t $IMAGE -f demo/Dockerfile ." >&2
    exit 1
}

cleanup
docker run --rm -d --name "$CONTAINER" -p "${PORT}:8000" \
    --cap-add=SYS_PTRACE "$IMAGE" >/dev/null

# Wait for the app rather than sleeping a guessed amount, so the recording
# starts at the same point every time.
for _ in $(seq 1 100); do
    if curl -fs --max-time 1 "${BASE}/health" >/dev/null 2>&1; then break; fi
    sleep 0.2
done

clear
note 'the app is up and answering normally'
run curl -s "${BASE}/widgets"
sleep 1

note 'arm the lock-order deadlock'
run curl -s -X POST "${BASE}/deadlock"
sleep 1

note 'this request will never come back'
printf '%s$ curl -s %s/reports/daily%s\n' "$BOLD" "$BASE" "$RESET"
curl -s --max-time 12 "${BASE}/reports/daily" >/dev/null 2>&1 &
HANGING=$!
sleep 3
printf '%s(no response -- still waiting)%s\n\n' "$DIM" "$RESET"

note 'no restart, no gdb: ask the running process what it is stuck on'
run_pipeline "docker exec $CONTAINER pidprobe snap 1 |
  jq -c '.stacks.threads[] | select(.frames[0].file | endswith(\"app.py\"))
         | {thread: .name, stuck_at: \"\(.frames[0].function):\(.frames[0].line)\"}'"
sleep 2

note 'and each of them is holding a pooled connection hostage'
run_pipeline "docker exec $CONTAINER pidprobe snap 1 | jq -c '.sqlalchemy.pools[0]'"
sleep 2

wait "$HANGING" 2>/dev/null || true
