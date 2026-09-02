#!/bin/sh
set -eu

if [ -n "${RAILWAY_VOLUME_MOUNT_PATH:-}" ]; then
    persistent_root="$RAILWAY_VOLUME_MOUNT_PATH"
    export BUYEYE_DATA_DIR="$persistent_root"
    export BUYEYE_DB_PATH="$persistent_root/buyeeye.db"
    export BUYEYE_SESSION_DIR="$persistent_root/browser"
    export BUYEYE_CONFIG_DIR="/app/config"
else
    persistent_root="${BUYEYE_DATA_DIR:-/data}"
fi

mkdir -p "$persistent_root/browser" "$persistent_root/logs"

if [ -f "$persistent_root/browser/buyee_state.json" ]; then
    session_status="present"
else
    session_status="missing"
fi

validation_mode=0
if [ "${BUYEYE_START_MODE:-run}" = "validate" ]; then
    validation_mode=1
    validation_query="${BUYEYE_VALIDATION_QUERY:-Number (N)ine}"
    set -- python -m buyeeye scan-once "$validation_query"
fi

display="${DISPLAY:-:99}"
export DISPLAY="$display"

echo "BuyEye bootstrap: data=$persistent_root session=$session_status display=$display"

Xvfb "$display" -screen 0 1440x1100x24 -nolisten tcp -ac &
xvfb_pid=$!

for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    if [ -S "/tmp/.X11-unix/X${display#:}" ]; then
        if [ "$validation_mode" -eq 1 ]; then
            python -m buyeeye health
        fi
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
