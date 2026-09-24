import 'dart:async';
import 'dart:typed_data';

import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/models.dart';

/// A configurable in-memory gateway shared by the controller and UI tests.
final class FakeGateway
    implements
        GatewayApi,
        GatewayFilesApi,
        GatewayMessagingApi,
        GatewayContactsApi,
        GatewayAdminApi {
  FakeGateway({
    List<RackUnit>? units,
    this.eventsValue = const [],
    this.telemetryValue,
    this.telemetryFailure,
    this.statsValue,
    this.healthValue,
  }) : unitsValue = units ?? [unit()];

  List<RackUnit> unitsValue;
  List<GatewayEvent> eventsValue;
  UnitTelemetry? telemetryValue;
  Object? telemetryFailure;
  Object? eventsFailure;
  GatewayStats? statsValue;
  GatewayHealth? healthValue;
  final List<String> removed = [];
  final List<({String? kind, String? unit})> eventQueries = [];
  int cancelledStreams = 0;
  final List<({String unit, String to, String body})> sent = [];
  List<Contact> contactsValue = const [];
  Object? contactsFailure;
  final List<bool> contactReads = [];
  List<Session> sessionsValue = const [];
  List<AuditEntry> auditValue = const [];
  final List<int> revoked = [];
  int revokedAll = 0;
  final List<String> totpPasswords = [];
  String scopeGranted = 'control';
  Object? sendFailure;

  // A fresh controller per call, because every real `stream()` is a new HTTP
  // request - one single-subscription stream handed out twice cannot be
  // listened to again after a cancel, which is exactly what a reconnect does.
  final List<StreamController<GatewayEvent>> streams = [];

  void add(GatewayEvent event) => streams.last.add(event);
  void fail(Object failure) => streams.last.addError(failure);

  @override
  Future<List<GatewayEvent>> events({
    String? kind,
    String? unit,
    int? since,
    int? limit,
  }) async {
    eventQueries.add((kind: kind, unit: unit));
    final failure = eventsFailure;
    if (failure != null) throw failure;
    return eventsValue
        .where((event) => kind == null || event.kind == kind)
        .where((event) => unit == null || event.unit == unit)
        .toList();
  }

  @override
  Future<List<Contact>> contacts(String unit, {bool refresh = false}) async {
    contactReads.add(refresh);
    final failure = contactsFailure;
    if (failure != null) throw failure;
    return contactsValue;
  }

  @override
  Future<GatewayEvent> sendMessage(String unit, String to, String body) async {
    final failure = sendFailure;
    if (failure != null) throw failure;
    sent.add((unit: unit, to: to, body: body));
    return event(
      1000 + sent.length,
      unit: unit,
      address: to,
      body: body,
      direction: 'out',
    );
  }

  @override
  Stream<GatewayEvent> stream() {
    late final StreamController<GatewayEvent> controller;
    controller = StreamController<GatewayEvent>(
      onCancel: () => cancelledStreams++,
    );
    streams.add(controller);
    return controller.stream;
  }

  @override
  Future<UnitTelemetry> telemetry(String unit) async {
    final failure = telemetryFailure;
    if (failure != null) throw failure;
    return telemetryValue ?? makeTelemetry(up: true, unit: unit);
  }

  @override
  Future<GatewayStats> stats() async => statsValue ?? makeStats(totp: true);

  @override
  Future<GatewayHealth> health() async =>
      healthValue ??
      const GatewayHealth(
        status: 'ok',
        version: '9.9.9',
        ntfyEnabled: false,
        totpEnabled: true,
      );

  @override
  Future<List<RackUnit>> units() async => unitsValue;

  @override
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) async => Tokens(
    refreshToken: 'refresh',
    accessToken: 'access',
    scope: scope == 'admin' ? scopeGranted : scope,
    refreshExpiresAt: 1,
    accessExpiresAt: 1,
  );

  @override
  Future<List<Session>> sessions() async => sessionsValue;

  @override
  Future<void> revokeSession(int id) async => revoked.add(id);

  @override
  Future<void> revokeAllSessions() async => revokedAll++;

  @override
  Future<List<AuditEntry>> audit({int? limit}) async => auditValue;

  @override
  Future<TotpEnrollment> enableTotp(String password) async {
    totpPasswords.add(password);
    return const TotpEnrollment(
      secret: 'JBSWY3DPEHPK3PXP',
      recoveryCodes: ['aaaa-1111', 'bbbb-2222'],
    );
  }

  @override
  Future<void> disableTotp(String password) async =>
      totpPasswords.add(password);

  @override
  Future<void> logOut(String refreshToken) async {}

  @override
  Future<Tokens> refresh(String refreshToken) => throw UnimplementedError();

  @override
  Future<List<GatewayEvent>> messages({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();

  @override
  Future<List<GatewayEvent>> calls({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();

  @override
  Future<List<UnitFile>> files(String unit) async => const [
    UnitFile(name: 'payload.bin', size: 4, modifiedAt: 1700000000),
  ];

  @override
  Future<void> removeFile(String unit, String name) async => removed.add(name);

  @override
  Future<Uint8List> downloadFile(String unit, String name) =>
      throw UnimplementedError();

  @override
  Future<void> uploadFile(String unit, String name, Uint8List bytes) =>
      throw UnimplementedError();

  @override
  void close() {
    for (final controller in streams) {
      controller.close();
    }
  }
}

RackUnit unit({
  String name = 'lisa01',
  Set<String> capabilities = const {'sms', 'notifications'},
}) => RackUnit(name: name, label: name, capabilities: capabilities);

/// An event [id] minutes after a fixed instant, in the device's milliseconds.
GatewayEvent event(
  int id, {
  String kind = 'sms',
  String unit = 'lisa01',
  String? address,
  String? body,
  String direction = 'in',
  int? duration,
}) => GatewayEvent(
  id: id,
  unit: unit,
  kind: kind,
  address: address ?? 'sender $id',
  body: body ?? 'body $id',
  timestamp: 1700000000000 + id * 60000,
  direction: direction,
  duration: duration,
  receivedAt: 1700000100 + id,
);

UnitTelemetry makeTelemetry({required bool up, String unit = 'lisa01'}) =>
    UnitTelemetry(
      unit: unit,
      up: up,
      collectedAt: 1700000000,
      samples: const {
        'rackphone_battery_capacity_percent': 72,
        'rackphone_temperature_celsius': 31.5,
        'rackphone_uptime_seconds': 3 * 86400 + 60,
      },
    );

GatewayStats makeStats({required bool totp}) => GatewayStats(
  eventsByKind: const {'sms': 3, 'call': 1},
  drained: 0,
  stored: 4,
  filtered: 0,
  pushed: 0,
  pushFailed: 0,
  errors: 0,
  security: SecuritySummary(
    lastLoginAt: 1700000000,
    lastLoginDevice: 'Desk',
    failedLogins24h: 2,
    lockedUntil: null,
    totpEnabled: totp,
  ),
);
