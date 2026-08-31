#!/bin/sh
set -eu

display="${DISPLAY:-:99}"
export DISPLAY="$display"

Xvfb "$display" -screen 0 1440x1100x24 -nolisten tcp -ac &
xvfb_pid=$!

for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    if [ -S "/tmp/.X11-unix/X${display#:}" ]; then
        exec "$@"
    fi
    if ! kill -0 "$xvfb_pid" 2>/dev/null; then
        echo "Virtual display failed to start." >&2
        exit 1
    fi
    sleep 0.2
done

echo "Virtual display did not become ready." >&2
exit 1
