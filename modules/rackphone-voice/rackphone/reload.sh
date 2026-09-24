#!/system/bin/sh
set -u
# Settings are read when a bridge is started. Reconfiguring a live call would
# change its rate mid-stream, so a change applies to the next bridge, not this.
echo "voice settings will apply to the next call bridge"
