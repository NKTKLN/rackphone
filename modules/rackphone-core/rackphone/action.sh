#!/system/bin/sh
case "${1:-}" in
  selinux_permissive) setenforce 0 && echo "SELinux: permissive" ;;
  selinux_enforcing)  setenforce 1 && echo "SELinux: enforcing" ;;
  *) echo "unknown action: ${1:-}" >&2; exit 2 ;;
esac
