#!/system/bin/sh
#
# Event sources. Each emits newline-delimited JSON objects on stdout.
#
# Read with sqlite3 in read-only mode rather than through `content query`,
# because that command's output is genuinely ambiguous: it prints
# `col=value, col=value`, so a message body containing ", " is
# indistinguishable from a column break. json_object() escapes correctly and
# cannot be confused by content.
#
# The cost of that choice: the on-disk schema is not a public API, so a
# LineageOS upgrade could move it. `selftest` exists to catch that quickly, and
# the queries touch only long-stable columns.
set -u

# Prefixable so the collectors can run on a workstation against a fixture
# database. Empty in production, so the tested code path is the shipped one.
DATA=${RACKPHONE_DATA_ROOT:-}
TELDB=$DATA/data/data/com.android.providers.telephony/databases/mmssms.db
CALLDB=$DATA/data/data/com.android.providers.contacts/databases/calllog.db
# Older Android kept the call log inside contacts2.db.
[ -f "$CALLDB" ] || CALLDB=$DATA/data/data/com.android.providers.contacts/databases/contacts2.db

# sq <db> <sql> - read-only, so the collector can never modify the user's data.
sq() {
  [ -f "$1" ] || return 1
  sqlite3 -readonly "$1" "$2" 2>/dev/null
}

sources_available() {
  _s=""
  [ -f "$TELDB" ]  && _s="$_s sms"
  [ -f "$CALLDB" ] && _s="$_s calls"
  echo "${_s# }"
}

# sms_since <cursor-id> <include-body> [limit]
# type = 1 is the inbox. Sent messages are never relayed.
sms_since() {
  _body="null"
  [ "$2" = "1" ] && _body="body"
  sq "$TELDB" "
    SELECT json_object(
      'kind','sms',
      'id', _id,
      'thread', thread_id,
      'address', address,
      'body', $_body,
      'ts', date,
      'sent_ts', date_sent,
      'sub', sub_id,
      'direction','in'
    )
    FROM sms
    WHERE _id > $1 AND type = 1
    ORDER BY _id ASC
    LIMIT ${3:-500};"
}

sms_max_id() { sq "$TELDB" "SELECT COALESCE(MAX(_id),0) FROM sms;"; }

# call_type_filter <spec> -> a SQL IN-list of android.provider.CallLog types.
# 1 incoming, 2 outgoing, 3 missed, 5 rejected, 6 blocked.
# Outgoing is absent from every option but "all": this relay is about what
# arrives at the unit, not what it did.
call_type_filter() {
  case "$1" in
    all)                  echo "1,2,3,5,6" ;;
    in)                   echo "1" ;;
    missed)               echo "3" ;;
    "in,missed,rejected") echo "1,3,5" ;;
    *)                    echo "1,3" ;;
  esac
}

# calls_since <cursor-id> <call-types> [limit]
calls_since() {
  _types=$(call_type_filter "$2")
  sq "$CALLDB" "
    SELECT json_object(
      'kind','call',
      'id', _id,
      'address', number,
      'ts', date,
      'duration', duration,
      'sub', subscription_id,
      'direction', CASE type
        WHEN 1 THEN 'in' WHEN 2 THEN 'out' WHEN 3 THEN 'missed'
        WHEN 5 THEN 'rejected' WHEN 6 THEN 'blocked' ELSE 'other' END
    )
    FROM calls
    WHERE _id > $1 AND type IN ($_types)
    ORDER BY _id ASC
    LIMIT ${3:-200};"
}

calls_max_id() { sq "$CALLDB" "SELECT COALESCE(MAX(_id),0) FROM calls;"; }

# Reports whether each source can actually be read, for the selftest action.
sources_selftest() {
  printf 'sms_db      %s\n' "$([ -f "$TELDB" ] && echo present || echo MISSING)"
  printf 'sms_query   %s\n' "$(sms_max_id >/dev/null 2>&1 && echo ok || echo FAILED)"
  printf 'call_db     %s\n' "$([ -f "$CALLDB" ] && echo present || echo MISSING)"
  printf 'call_query  %s\n' "$(calls_max_id >/dev/null 2>&1 && echo ok || echo FAILED)"
  printf 'sqlite3     %s\n' "$(command -v sqlite3 >/dev/null 2>&1 && sqlite3 --version 2>/dev/null | cut -d' ' -f1 || echo MISSING)"
}
