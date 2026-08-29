/// The shapes the native side reports, and the rules the form applies.
///
/// Everything here is parsed from `status.json` - the same document a root
/// shell reads off the unit over adb. Keeping the UI on the host's data format
/// means a screen that disagrees with the host is a bug in one place, not two.
library;

/// Keepalive interval bounds, mirrored from `Config.kt`.
const int minIntervalHours = 1;
const int maxIntervalHours = 8760;

/// The `keepalive_to` value that means "this unit's own number".
const String targetSelf = 'self';

/// `keepalive_subs` values that are not a list of ids.
const String subsAll = 'all';
const String subsDefault = 'default';

/// One active subscription on the device.
class SimInfo {
  const SimInfo({
    required this.subId,
    required this.slot,
    required this.carrier,
    required this.label,
    required this.number,
    required this.isDefaultSms,
  });

  factory SimInfo.fromJson(Map<String, dynamic> json) => SimInfo(
    subId: _int(json['sub_id']) ?? -1,
    slot: _int(json['slot']) ?? -1,
    carrier: _string(json['carrier']),
    label: _string(json['label']),
    number: _string(json['number']),
    isDefaultSms: json['is_default_sms'] == true,
  );

  final int subId;
  final int slot;
  final String carrier;
  final String label;
  final String number;
  final bool isDefaultSms;

  /// What to show in a list where the carrier is often blank.
  String get title {
    final name = carrier.isNotEmpty ? carrier : label;
    final slotLabel = slot >= 0 ? 'SIM ${slot + 1}' : 'SIM';
    return name.isNotEmpty ? '$slotLabel · $name' : slotLabel;
  }
}

/// One subscription's keepalive clock.
///
/// Per SIM because an operator only ever sees its own: keeping one alive says
/// nothing about the other, and a single device-wide line would let the SIM
/// that is quietly dying hide behind the one the host uses daily.
class KeepaliveTarget {
  const KeepaliveTarget({
    required this.subId,
    required this.to,
    required this.resolves,
    required this.lastSuccessMs,
    required this.nextDueMs,
  });

  factory KeepaliveTarget.fromJson(Map<String, dynamic> json) =>
      KeepaliveTarget(
        subId: _int(json['sub_id']) ?? -1,
        to: _string(json['to']),
        resolves: json['resolves'] == true,
        lastSuccessMs: _int(json['last_success_ms']) ?? 0,
        nextDueMs: _int(json['next_due_ms']),
      );

  final int subId;

  /// The number this SIM will text, once `self` has been resolved through it.
  final String to;

  /// Whether that number exists. False with `self` on a SIM that does not
  /// carry its own number: the case where the schedule looks healthy and
  /// nothing would ever be sent.
  final bool resolves;

  final int lastSuccessMs;
  final int? nextDueMs;
}

/// The schedule, as the native side currently sees it.
class KeepaliveStatus {
  const KeepaliveStatus({
    required this.enabled,
    required this.to,
    required this.subs,
    required this.intervalHours,
    required this.nextDueMs,
    required this.targets,
  });

  factory KeepaliveStatus.fromJson(Map<String, dynamic> json) =>
      KeepaliveStatus(
        enabled: json['enabled'] == true,
        to: _string(json['to'], fallback: targetSelf),
        subs: _string(json['subs'], fallback: subsAll),
        intervalHours: _int(json['interval_hours']) ?? 720,
        nextDueMs: _int(json['next_due_ms']),
        targets: (json['targets'] as List<dynamic>? ?? const [])
            .map((dynamic e) => KeepaliveTarget.fromJson(_map(e)))
            .toList(growable: false),
      );

  final bool enabled;
  final String to;

  /// Which SIMs are covered: [subsAll], [subsDefault], or a list of ids.
  final String subs;

  final int intervalHours;

  /// The soonest any covered SIM is due.
  final int? nextDueMs;

  final List<KeepaliveTarget> targets;

  bool get isSelf => to.trim().isEmpty || to.trim().toLowerCase() == targetSelf;

  bool get coversAllSims => subs.trim().toLowerCase() != subsDefault;

  /// SIMs that would send nothing if the alarm fired right now.
  List<KeepaliveTarget> get unresolved =>
      targets.where((KeepaliveTarget t) => !t.resolves).toList(growable: false);
}

/// The last balance one SIM reported over USSD.
///
/// The raw text is kept next to the parsed number because the parse is a
/// guess: every operator words this differently, and the sentence is the
/// evidence for what the number means.
class BalanceReading {
  const BalanceReading({
    required this.subId,
    required this.amount,
    required this.text,
    required this.checkedMs,
  });

  factory BalanceReading.fromJson(Map<String, dynamic> json) => BalanceReading(
    subId: _int(json['sub_id']) ?? -1,
    amount: json['amount'] is num ? (json['amount'] as num).toDouble() : null,
    text: _string(json['text']),
    checkedMs: _int(json['checked_ms']) ?? 0,
  );

  final int subId;

  /// Null when the reply carried no number the parser recognised.
  final double? amount;

  final String text;
  final int checkedMs;

  /// What to show: the number when there is one, the operator's words when
  /// there is not.
  String get label => amount != null ? amount!.toStringAsFixed(2) : text;
}

/// What the unit is collecting, and how much of it is waiting for the host.
class InboxStatus {
  const InboxStatus({
    required this.collectSms,
    required this.collectCalls,
    required this.includeBody,
    required this.pending,
    required this.dropped,
    required this.cap,
  });

  factory InboxStatus.fromJson(Map<String, dynamic> json) => InboxStatus(
    collectSms: json['collect_sms'] != false,
    collectCalls: json['collect_calls'] != false,
    includeBody: json['include_body'] != false,
    pending: _int(json['pending']) ?? 0,
    dropped: _int(json['dropped']) ?? 0,
    cap: _int(json['cap']) ?? 2000,
  );

  final bool collectSms;
  final bool collectCalls;

  /// Off relays sender and time without the text, so message content never
  /// leaves the phone.
  final bool includeBody;

  /// Events spooled but not yet acked by the host.
  final int pending;

  /// Events discarded because the spool hit its cap: a host that stopped
  /// draining, counted rather than silent.
  final int dropped;

  final int cap;
}

/// Lifetime counters. Cheap to keep, and the first thing to look at.
class Counters {
  const Counters({
    required this.sentOk,
    required this.sentFailed,
    required this.rejected,
    required this.pending,
  });

  factory Counters.fromJson(Map<String, dynamic> json) => Counters(
    sentOk: _int(json['sent_ok']) ?? 0,
    sentFailed: _int(json['sent_failed']) ?? 0,
    rejected: _int(json['rejected']) ?? 0,
    pending: _int(json['pending']) ?? 0,
  );

  final int sentOk;
  final int sentFailed;
  final int rejected;
  final int pending;
}

/// One line of the outbox.
class SendRecord {
  const SendRecord({
    required this.id,
    required this.ts,
    required this.to,
    required this.source,
    required this.status,
    required this.error,
    required this.bodyChars,
  });

  factory SendRecord.fromJson(Map<String, dynamic> json) => SendRecord(
    id: _string(json['id']),
    ts: _int(json['ts']) ?? 0,
    to: _string(json['to']),
    source: _string(json['source']),
    status: _string(json['status'], fallback: 'unknown'),
    error: _string(json['error']),
    bodyChars: _int(json['body_chars']) ?? 0,
  );

  final String id;
  final int ts;
  final String to;
  final String source;

  /// `queued`, `ok`, `failed` or `rejected`. A send appears twice: once when
  /// the radio accepts it and once when it resolves.
  final String status;
  final String error;
  final int bodyChars;

  bool get isFailure => status == 'failed' || status == 'rejected';
}

/// Everything `status.json` says.
class CompanionStatus {
  const CompanionStatus({
    required this.ready,
    required this.tokenSet,
    required this.canSendSms,
    required this.canReceiveSms,
    required this.canReadCallLog,
    required this.canReadPhoneState,
    required this.batteryExempt,
    required this.standbyBucket,
    required this.subId,
    required this.sims,
    required this.keepalive,
    required this.balances,
    required this.inbox,
    required this.counters,
    required this.lastSend,
  });

  factory CompanionStatus.fromJson(Map<String, dynamic> json) {
    final permissions = _map(json['permissions']);
    final last = json['last_send'];
    return CompanionStatus(
      ready: json['ready'] == true,
      tokenSet: json['token_set'] == true,
      canSendSms: permissions['send_sms'] == true,
      canReceiveSms: permissions['receive_sms'] == true,
      canReadCallLog: permissions['read_call_log'] == true,
      canReadPhoneState: permissions['read_phone_state'] == true,
      batteryExempt: _map(json['power'])['battery_exempt'] == true,
      standbyBucket: _string(
        _map(json['power'])['standby_bucket'],
        fallback: 'unknown',
      ),
      subId: _int(json['sub_id']) ?? -1,
      sims: (json['sims'] as List<dynamic>? ?? const [])
          .map((dynamic e) => SimInfo.fromJson(_map(e)))
          .toList(growable: false),
      keepalive: KeepaliveStatus.fromJson(_map(json['keepalive'])),
      balances: (json['balances'] as List<dynamic>? ?? const [])
          .map((dynamic e) => BalanceReading.fromJson(_map(e)))
          .toList(growable: false),
      inbox: InboxStatus.fromJson(_map(json['inbox'])),
      counters: Counters.fromJson(_map(json['counters'])),
      lastSend: last is Map ? SendRecord.fromJson(_map(last)) : null,
    );
  }

  final bool ready;
  final bool tokenSet;
  final bool canSendSms;
  final bool canReceiveSms;
  final bool canReadCallLog;
  final bool canReadPhoneState;

  /// Whether the system will run this app's alarms when they come due. Without
  /// the exemption Android defers them by up to a year, and the keepalive
  /// becomes a schedule nobody executes.
  final bool batteryExempt;

  /// `active`, `working_set`, `frequent`, `rare`, `restricted`.
  final String standbyBucket;
  final int subId;
  final List<SimInfo> sims;
  final KeepaliveStatus keepalive;
  final List<BalanceReading> balances;
  final InboxStatus inbox;
  final Counters counters;
  final SendRecord? lastSend;

  /// The last balance reported for one SIM, if it has ever answered.
  BalanceReading? balanceFor(int subId) {
    for (final reading in balances) {
      if (reading.subId == subId) return reading;
    }
    return null;
  }

  /// Why the unit cannot send, in the order worth fixing. Empty when it can.
  List<String> get blockers => <String>[
    if (!canSendSms) 'SEND_SMS is not granted',
    if (!canReceiveSms) 'RECEIVE_SMS is not granted',
    if (!batteryExempt) 'Battery optimisation defers the keepalive alarm',
    // Without it a call arrives with its number withheld: the fact of the call
    // is relayed, but not who made it.
    if (!canReadCallLog) 'READ_CALL_LOG is not granted, so callers are unnamed',
    if (sims.isEmpty && !canReadPhoneState)
      'No SIM visible without READ_PHONE_STATE',
    if (!tokenSet) 'No control token, so the host cannot drive this app',
  ];
}

/// Reject a destination the radio would refuse, mirroring `Numbers.kt`.
///
/// Duplicated on purpose: the form has to answer while the person is typing,
/// and a round trip per keystroke to say "that is not a number" would be worse
/// than keeping the rule in two places that are each five lines long.
String? validateTarget(String raw, {bool allowSelf = true}) {
  final value = raw.trim();
  if (value.isEmpty) return allowSelf ? null : 'Enter a number';
  if (allowSelf && value.toLowerCase() == targetSelf) return null;

  final cleaned = value.replaceAll(RegExp(r'[\s–\-()./]'), '');
  if (!RegExp(r'^\+?\d{1,20}$').hasMatch(cleaned)) {
    return allowSelf ? 'Use a phone number, or "self"' : 'Use a phone number';
  }
  return null;
}

/// Parse and bound an interval typed into the form.
String? validateIntervalHours(String raw) {
  final hours = int.tryParse(raw.trim());
  if (hours == null) return 'Whole hours';
  if (hours < minIntervalHours || hours > maxIntervalHours) {
    return 'Between $minIntervalHours and $maxIntervalHours hours';
  }
  return null;
}

/// Render an interval the way it was most likely meant.
String describeInterval(int hours) {
  if (hours % 24 == 0 && hours >= 24) {
    final days = hours ~/ 24;
    return days == 1 ? 'every day' : 'every $days days';
  }
  return hours == 1 ? 'every hour' : 'every $hours hours';
}

/// Render a moment as a distance from now, which is the only thing anyone
/// reads it for. Absolute timestamps go in the outbox, not on a status line.
String describeWhen(int? ms, {required DateTime now}) {
  if (ms == null || ms <= 0) return 'never';
  final at = DateTime.fromMillisecondsSinceEpoch(ms);
  final delta = at.difference(now);
  final ahead = !delta.isNegative;
  final span = delta.abs();

  final String amount;
  if (span.inMinutes < 1) {
    amount = 'moments';
  } else if (span.inHours < 1) {
    amount = '${span.inMinutes} min';
  } else if (span.inHours < 48) {
    amount = '${span.inHours} h';
  } else {
    amount = '${span.inDays} days';
  }
  return ahead ? 'in $amount' : '$amount ago';
}

Map<String, dynamic> _map(dynamic value) =>
    value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};

int? _int(dynamic value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value);
  return null;
}

String _string(dynamic value, {String fallback = ''}) {
  if (value == null) return fallback;
  if (value is String) return value;
  return value.toString();
}
