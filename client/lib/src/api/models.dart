/// Tolerant, immutable representations of the gateway's public JSON.
library;

/// Metadata for one regular file in a unit's confined transfer directory.
class UnitFile {
  const UnitFile({
    required this.name,
    required this.size,
    required this.modifiedAt,
  });

  factory UnitFile.fromJson(Map<String, dynamic> json) => UnitFile(
    name: _string(json['name']),
    size: _int(json['size']) ?? 0,
    modifiedAt: _int(json['modified_at']) ?? 0,
  );

  final String name;
  final int size;
  final int modifiedAt;

  DateTime get modified => DateTime.fromMillisecondsSinceEpoch(
    modifiedAt * Duration.millisecondsPerSecond,
    isUtc: true,
  );

  @override
  bool operator ==(Object other) =>
      other is UnitFile &&
      name == other.name &&
      size == other.size &&
      modifiedAt == other.modifiedAt;

  @override
  int get hashCode => Object.hash(name, size, modifiedAt);
}

/// A short-lived access token paired with the refresh token that rotates it.
///
/// Expiry values stay as Unix seconds because that is the signed server
/// contract; the convenience getter keeps clock conversion out of callers.
class Tokens {
  const Tokens({
    required this.refreshToken,
    required this.accessToken,
    required this.scope,
    required this.refreshExpiresAt,
    required this.accessExpiresAt,
  });

  factory Tokens.fromJson(Map<String, dynamic> json) => Tokens(
    refreshToken: _string(json['refresh_token']),
    accessToken: _string(json['access_token']),
    scope: _string(json['scope']),
    refreshExpiresAt: _int(json['refresh_expires_at']) ?? 0,
    accessExpiresAt: _int(json['access_expires_at']) ?? 0,
  );

  final String refreshToken;
  final String accessToken;
  final String scope;
  final int refreshExpiresAt;
  final int accessExpiresAt;

  DateTime get accessExpiry => DateTime.fromMillisecondsSinceEpoch(
    accessExpiresAt * Duration.millisecondsPerSecond,
    isUtc: true,
  );

  @override
  bool operator ==(Object other) =>
      other is Tokens &&
      refreshToken == other.refreshToken &&
      accessToken == other.accessToken &&
      scope == other.scope &&
      refreshExpiresAt == other.refreshExpiresAt &&
      accessExpiresAt == other.accessExpiresAt;

  @override
  int get hashCode => Object.hash(
    refreshToken,
    accessToken,
    scope,
    refreshExpiresAt,
    accessExpiresAt,
  );
}

/// One unit even when it has not produced an event yet.
class RackUnit {
  RackUnit({
    required this.name,
    required this.label,
    required Set<String> capabilities,
  }) : capabilities = Set.unmodifiable(capabilities);

  factory RackUnit.fromJson(Map<String, dynamic> json) => RackUnit(
    name: _string(json['name']),
    label: _string(json['label']),
    capabilities: _stringSet(json['capabilities']),
  );

  final String name;
  final String label;

  /// Host-declared rights are a set because order has no policy meaning.
  final Set<String> capabilities;

  bool can(String capability) => capabilities.contains(capability);

  @override
  bool operator ==(Object other) =>
      other is RackUnit &&
      name == other.name &&
      label == other.label &&
      _setEquals(capabilities, other.capabilities);

  @override
  int get hashCode =>
      Object.hash(name, label, Object.hashAllUnordered(capabilities));
}

/// One row accepted into the host's event store.
class GatewayEvent {
  const GatewayEvent({
    required this.id,
    required this.unit,
    required this.kind,
    required this.address,
    required this.body,
    required this.timestamp,
    required this.direction,
    required this.duration,
    required this.receivedAt,
  });

  factory GatewayEvent.fromJson(Map<String, dynamic> json) => GatewayEvent(
    id: _int(json['id']) ?? 0,
    unit: _string(json['unit']),
    kind: _string(json['kind']),
    address: _nullableString(json['address']),
    body: _nullableString(json['body']),
    timestamp: _int(json['ts']),
    direction: _nullableString(json['direction']),
    duration: _int(json['duration']),
    receivedAt: _int(json['received_at']),
  );

  final int id;
  final String unit;
  final String kind;
  final String? address;
  final String? body;

  /// The device's clock; [receivedAt] is the host's clock. Mixing them makes
  /// a feed jump whenever a phone clock differs from the gateway clock.
  final int? timestamp;

  final String? direction;
  final int? duration;
  final int? receivedAt;

  @override
  bool operator ==(Object other) =>
      other is GatewayEvent &&
      id == other.id &&
      unit == other.unit &&
      kind == other.kind &&
      address == other.address &&
      body == other.body &&
      timestamp == other.timestamp &&
      direction == other.direction &&
      duration == other.duration &&
      receivedAt == other.receivedAt;

  @override
  int get hashCode => Object.hash(
    id,
    unit,
    kind,
    address,
    body,
    timestamp,
    direction,
    duration,
    receivedAt,
  );
}

/// A current, finite subset of one unit's exported Prometheus samples.
class UnitTelemetry {
  UnitTelemetry({
    required this.unit,
    required this.up,
    required this.collectedAt,
    required Map<String, double> samples,
  }) : samples = Map.unmodifiable(samples);

  factory UnitTelemetry.fromJson(Map<String, dynamic> json) => UnitTelemetry(
    unit: _string(json['unit']),
    up: json['up'] == true,
    collectedAt: _int(json['collected_at']) ?? 0,
    samples: _doubleMap(json['samples']),
  );

  final String unit;
  final bool up;
  final int collectedAt;
  final Map<String, double> samples;

  double? get batteryPercent => samples['rackphone_battery_capacity_percent'];
  double? get batteryTemperature =>
      samples['rackphone_battery_temperature_celsius'];
  double? get skinTemperature => samples['rackphone_temperature_celsius'];
  double? get uptime => samples['rackphone_uptime_seconds'];

  @override
  bool operator ==(Object other) =>
      other is UnitTelemetry &&
      unit == other.unit &&
      up == other.up &&
      collectedAt == other.collectedAt &&
      _mapEquals(samples, other.samples);

  @override
  int get hashCode => Object.hash(
    unit,
    up,
    collectedAt,
    Object.hashAllUnordered(
      samples.entries.map((entry) => Object.hash(entry.key, entry.value)),
    ),
  );
}

/// Authentication posture included in new gateway statistics responses.
class SecuritySummary {
  const SecuritySummary({
    required this.lastLoginAt,
    required this.lastLoginDevice,
    required this.failedLogins24h,
    required this.lockedUntil,
    required this.totpEnabled,
  });

  factory SecuritySummary.fromJson(Map<String, dynamic> json) =>
      SecuritySummary(
        lastLoginAt: _int(json['last_login_at']),
        lastLoginDevice: _nullableString(json['last_login_device']),
        failedLogins24h: _int(json['failed_logins_24h']) ?? 0,
        lockedUntil: _int(json['locked_until']),
        totpEnabled: json['totp_enabled'] == true,
      );

  final int? lastLoginAt;
  final String? lastLoginDevice;
  final int failedLogins24h;
  final int? lockedUntil;
  final bool totpEnabled;

  @override
  bool operator ==(Object other) =>
      other is SecuritySummary &&
      lastLoginAt == other.lastLoginAt &&
      lastLoginDevice == other.lastLoginDevice &&
      failedLogins24h == other.failedLogins24h &&
      lockedUntil == other.lockedUntil &&
      totpEnabled == other.totpEnabled;

  @override
  int get hashCode => Object.hash(
    lastLoginAt,
    lastLoginDevice,
    failedLogins24h,
    lockedUntil,
    totpEnabled,
  );
}

/// Store totals and optional live-drain counters from the same snapshot.
class GatewayStats {
  GatewayStats({
    required Map<String, int> eventsByKind,
    required this.drained,
    required this.stored,
    required this.filtered,
    required this.pushed,
    required this.pushFailed,
    required this.errors,
    this.security,
  }) : eventsByKind = Map.unmodifiable(eventsByKind);

  factory GatewayStats.fromJson(Map<String, dynamic> json) {
    final gateway = _map(json['gateway']);
    return GatewayStats(
      eventsByKind: _intMap(json['events']),
      drained: _int(gateway['drained']) ?? 0,
      stored: _int(gateway['stored']) ?? 0,
      filtered: _int(gateway['filtered']) ?? 0,
      pushed: _int(gateway['pushed']) ?? 0,
      pushFailed: _int(gateway['push_failed']) ?? 0,
      errors: _int(gateway['errors']) ?? 0,
      security: json['security'] is Map
          ? SecuritySummary.fromJson(_map(json['security']))
          : null,
    );
  }

  final Map<String, int> eventsByKind;
  final int drained;
  final int stored;
  final int filtered;
  final int pushed;
  final int pushFailed;
  final int errors;
  final SecuritySummary? security;

  @override
  bool operator ==(Object other) =>
      other is GatewayStats &&
      _mapEquals(eventsByKind, other.eventsByKind) &&
      drained == other.drained &&
      stored == other.stored &&
      filtered == other.filtered &&
      pushed == other.pushed &&
      pushFailed == other.pushFailed &&
      errors == other.errors &&
      security == other.security;

  @override
  int get hashCode => Object.hash(
    Object.hashAllUnordered(
      eventsByKind.entries.map((entry) => Object.hash(entry.key, entry.value)),
    ),
    drained,
    stored,
    filtered,
    pushed,
    pushFailed,
    errors,
    security,
  );
}

/// Non-sensitive gateway state suitable for an unauthenticated connection
/// check.
class GatewayHealth {
  const GatewayHealth({
    required this.status,
    required this.version,
    required this.ntfyEnabled,
    required this.totpEnabled,
  });

  factory GatewayHealth.fromJson(Map<String, dynamic> json) => GatewayHealth(
    status: _string(json['status']),
    version: _string(json['version']),
    ntfyEnabled: json['ntfy'] == 'enabled',
    totpEnabled: json['totp'] == 'enabled',
  );

  final String status;
  final String version;
  final bool ntfyEnabled;
  final bool totpEnabled;

  @override
  bool operator ==(Object other) =>
      other is GatewayHealth &&
      status == other.status &&
      version == other.version &&
      ntfyEnabled == other.ntfyEnabled &&
      totpEnabled == other.totpEnabled;

  @override
  int get hashCode => Object.hash(status, version, ntfyEnabled, totpEnabled);
}

/// Refresh-token metadata deliberately excludes the token and its stored hash.
class Session {
  const Session({
    required this.id,
    required this.deviceLabel,
    required this.scope,
    required this.issuedAt,
    required this.expiresAt,
    required this.lastSeen,
    required this.revokedAt,
  });

  factory Session.fromJson(Map<String, dynamic> json) => Session(
    id: _int(json['id']) ?? 0,
    deviceLabel: _string(json['device_label']),
    scope: _string(json['scope']),
    issuedAt: _int(json['issued_at']) ?? 0,
    expiresAt: _int(json['expires_at']) ?? 0,
    lastSeen: _int(json['last_seen']) ?? 0,
    revokedAt: _int(json['revoked_at']),
  );

  final int id;
  final String deviceLabel;
  final String scope;
  final int issuedAt;
  final int expiresAt;
  final int lastSeen;
  final int? revokedAt;

  @override
  bool operator ==(Object other) =>
      other is Session &&
      id == other.id &&
      deviceLabel == other.deviceLabel &&
      scope == other.scope &&
      issuedAt == other.issuedAt &&
      expiresAt == other.expiresAt &&
      lastSeen == other.lastSeen &&
      revokedAt == other.revokedAt;

  @override
  int get hashCode => Object.hash(
    id,
    deviceLabel,
    scope,
    issuedAt,
    expiresAt,
    lastSeen,
    revokedAt,
  );
}

int? _int(dynamic value) => value is num ? value.toInt() : null;

String _string(dynamic value) => value is String ? value : '';

String? _nullableString(dynamic value) => value is String ? value : null;

Map<String, dynamic> _map(dynamic value) => value is Map
    ? value.map((key, value) => MapEntry(key.toString(), value))
    : const {};

Set<String> _stringSet(dynamic value) =>
    value is List ? value.whereType<String>().toSet() : const <String>{};

Map<String, int> _intMap(dynamic value) {
  final result = <String, int>{};
  for (final entry in _map(value).entries) {
    final number = _int(entry.value);
    if (number != null) result[entry.key] = number;
  }
  return result;
}

Map<String, double> _doubleMap(dynamic value) {
  final result = <String, double>{};
  for (final entry in _map(value).entries) {
    final number = entry.value;
    if (number is num) result[entry.key] = number.toDouble();
  }
  return result;
}

bool _setEquals<T>(Set<T> left, Set<T> right) =>
    left.length == right.length && left.containsAll(right);

bool _mapEquals<K, V>(Map<K, V> left, Map<K, V> right) =>
    left.length == right.length &&
    left.entries.every((entry) => right[entry.key] == entry.value);
