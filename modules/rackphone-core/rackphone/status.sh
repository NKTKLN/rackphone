#!/system/bin/sh
echo "selinux=$(getenforce 2>/dev/null)"
echo "magisk=$(magisk -c 2>/dev/null || echo absent)"
echo "plugins=$(ls -d /data/adb/modules/*/rackphone 2>/dev/null | wc -l | tr -d ' ')"
echo "config=$([ -f /data/adb/rackphone/config.env ] && echo present || echo missing)"
