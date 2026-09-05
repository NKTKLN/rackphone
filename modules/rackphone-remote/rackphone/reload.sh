#!/system/bin/sh
set -u
# Settings are read when start is requested. Interrupting the current viewer on
# a config edit would violate session ownership, so reload affects the next one.
echo "remote settings will apply to the next session"
