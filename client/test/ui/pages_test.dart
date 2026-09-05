import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/errors.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/data/feed_controller.dart';
import 'package:rackphone_client/src/screen/decoder.dart';
import 'package:rackphone_client/src/screen/protocol.dart';
import 'package:rackphone_client/src/screen/screen_controller.dart';
import 'package:rackphone_client/src/screen/screen_socket.dart';
import 'package:rackphone_client/src/ui/pages/feed_page.dart';
import 'package:rackphone_client/src/ui/pages/messages_page.dart';
import 'package:rackphone_client/src/ui/pages/overview_page.dart';
import 'package:rackphone_client/src/ui/pages/screen_page.dart';

void main() {
  testWidgets('overview renders telemetry, stored counts, and security', (
    tester,
  ) async {
    final gateway = _PagesGateway(
      telemetryValue: _telemetry(up: true),
      statsValue: _stats(totp: false),
    );
    await tester.pumpWidget(
      _app(OverviewPage(gateway: gateway, unit: _unit())),
    );
    await tester.pump();

    expect(find.text('Battery: 72%'), findsOneWidget);
    expect(find.text('Temperature: 31.5 °C'), findsOneWidget);
    expect(find.text('Uptime: 1h 1m'), findsOneWidget);
    expect(find.text('Messages: 3'), findsOneWidget);
    expect(find.text('TOTP is off'), findsOneWidget);
    expect(find.textContaining('password is the only barrier'), findsOneWidget);
  });

  testWidgets('a refused telemetry call does not take the page down', (
    tester,
  ) async {
    final gateway = _PagesGateway(
      telemetryFailure: const GatewayForbiddenException(
        'unit capability denied',
      ),
      statsValue: _stats(totp: true),
    );
    await tester.pumpWidget(
      _app(OverviewPage(gateway: gateway, unit: _unit())),
    );
    await tester.pump();

    // The stored counts come from a different call and must survive the
    // refusal of this one.
    expect(find.text('Messages: 3'), findsOneWidget);
  });

  testWidgets('unanswered telemetry leaves stored counts visible', (
    tester,
  ) async {
    final gateway = _PagesGateway(
      telemetryValue: _telemetry(up: false),
      statsValue: _stats(totp: true),
    );
    await tester.pumpWidget(
      _app(OverviewPage(gateway: gateway, unit: _unit())),
    );
    await tester.pump();

    expect(find.textContaining('lisa01 did not answer'), findsOneWidget);
    expect(find.text('Messages: 3'), findsOneWidget);
    expect(find.textContaining('password is the only barrier'), findsNothing);
  });

  testWidgets('feed is newest first and describes an empty feed', (
    tester,
  ) async {
    final gateway = _PagesGateway(eventsValue: [_event(2), _event(1)]);
    final controller = FeedController(gateway: gateway, unit: 'lisa01');
    await tester.pumpWidget(_app(FeedPage(controller: controller)));
    await tester.pump();

    expect(
      tester.getTopLeft(find.text('new 2')).dy,
      lessThan(tester.getTopLeft(find.text('new 1')).dy),
    );

    await tester.pumpWidget(const SizedBox());
    final emptyGateway = _PagesGateway();
    final empty = FeedController(gateway: emptyGateway, unit: 'lisa01');
    await tester.pumpWidget(_app(FeedPage(controller: empty)));
    await tester.pump();
    expect(find.textContaining('from lisa01 will appear here'), findsOneWidget);
  });

  testWidgets('feed adds a streamed event without reloading', (tester) async {
    final gateway = _PagesGateway();
    final controller = FeedController(gateway: gateway, unit: 'lisa01');
    await tester.pumpWidget(_app(FeedPage(controller: controller)));
    await tester.pump();

    gateway.add(_event(4));
    await tester.pump();
    expect(find.text('new 4'), findsOneWidget);
    expect(gateway.eventLoads, 1);
  });

  testWidgets('messages lists only SMS and has no composer', (tester) async {
    final gateway = _PagesGateway(
      eventsValue: [
        _event(2),
        _event(1, kind: 'call'),
      ],
    );
    final controller = FeedController(gateway: gateway, unit: 'lisa01');
    await tester.pumpWidget(_app(MessagesPage(controller: controller)));
    await tester.pump();

    expect(find.text('new 2'), findsOneWidget);
    expect(find.text('new 1'), findsNothing);
    expect(
      find.text('Sending will arrive with the send route.'),
      findsOneWidget,
    );
    expect(find.byType(TextField), findsNothing);
  });

  testWidgets('screen distinguishes missing plugin from withheld capability', (
    tester,
  ) async {
    await tester.pumpWidget(
      _app(ScreenPage(unit: _unit(capabilities: {'screen'}))),
    );
    expect(find.textContaining('device-side plugin is built'), findsOneWidget);

    await tester.pumpWidget(_app(ScreenPage(unit: _unit())));
    expect(
      find.text('This gateway does not allow screen access for lisa01.'),
      findsOneWidget,
    );
  });

  testWidgets('held screen is distinct from a network failure', (tester) async {
    final held = _PageScreenConnection();
    final heldController = ScreenController(
      socketFactory: () async => held,
      decoder: FakeScreenDecoder(),
    );
    await tester.pumpWidget(
      _app(
        ScreenPage(
          unit: _unit(capabilities: {'screen'}),
          controller: heldController,
        ),
      ),
    );
    await tester.pump();
    held.finish(
      const ScreenCloseReason(
        ScreenCloseKind.heldByAnotherDevice,
        'session_busy Desk tablet',
      ),
    );
    // Two pumps: one for the completer's microtask to reach the controller,
    // one for the rebuild it asks for.
    await tester.pump();
    await tester.pump();
    expect(find.text('Screen is held by Desk tablet.'), findsOneWidget);
    expect(find.textContaining('failed'), findsNothing);
  });

  testWidgets('a network failure reads differently from a refusal', (
    tester,
  ) async {
    final failed = _PageScreenConnection();
    final failedController = ScreenController(
      socketFactory: () async => failed,
      decoder: FakeScreenDecoder(),
    );
    await tester.pumpWidget(
      _app(
        ScreenPage(
          unit: _unit(capabilities: {'screen'}),
          controller: failedController,
        ),
      ),
    );
    await tester.pump();
    failed.finish(
      const ScreenCloseReason(ScreenCloseKind.networkFailure, 'offline'),
    );
    await tester.pump();
    await tester.pump();
    expect(
      find.textContaining('Screen connection failed: offline'),
      findsOneWidget,
    );
    expect(find.textContaining('held by'), findsNothing);
  });
}

final class _PageScreenConnection implements ScreenConnection {
  final _closed = Completer<ScreenCloseReason>();

  void finish(ScreenCloseReason reason) => _closed.complete(reason);

  @override
  Future<ScreenCloseReason> get closed => _closed.future;
  @override
  Stream<DeviceInfo> get device => const Stream.empty();
  @override
  Stream<VideoPacket> get video => const Stream.empty();
  @override
  Future<void> close() async {}
  @override
  void send(List<int> controlMessage) {}
}

Widget _app(Widget child) => MaterialApp(home: Scaffold(body: child));

RackUnit _unit({Set<String> capabilities = const {'sms'}}) =>
    RackUnit(name: 'lisa01', label: 'Lisa', capabilities: capabilities);

UnitTelemetry _telemetry({required bool up}) => UnitTelemetry(
  unit: 'lisa01',
  up: up,
  collectedAt: 1700000000,
  samples: const {
    'rackphone_battery_capacity_percent': 72,
    'rackphone_temperature_celsius': 31.5,
    'rackphone_uptime_seconds': 3660,
  },
);

GatewayStats _stats({required bool totp}) => GatewayStats(
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

GatewayEvent _event(int id, {String kind = 'sms'}) => GatewayEvent(
  id: id,
  unit: 'lisa01',
  kind: kind,
  address: 'sender $id',
  body: 'new $id',
  timestamp: 1700000000 + id,
  direction: 'in',
  duration: null,
  receivedAt: 1700000100 + id,
);

final class _PagesGateway implements GatewayApi {
  _PagesGateway({
    this.telemetryValue,
    this.telemetryFailure,
    this.statsValue,
    this.eventsValue = const [],
  });

  final UnitTelemetry? telemetryValue;
  final Object? telemetryFailure;
  final GatewayStats? statsValue;
  final List<GatewayEvent> eventsValue;
  final StreamController<GatewayEvent> eventsStream =
      StreamController<GatewayEvent>.broadcast();
  int eventLoads = 0;

  void add(GatewayEvent event) => eventsStream.add(event);

  @override
  Future<UnitTelemetry> telemetry(String unit) {
    final failure = telemetryFailure;
    // Thrown synchronously on purpose: the page starts this call unawaited from
    // initState, so a failure that never becomes a future is the one that gets
    // past its handlers.
    if (failure != null) throw failure;
    return Future.value(telemetryValue!);
  }

  @override
  Future<GatewayStats> stats() async => statsValue!;
  @override
  Future<List<GatewayEvent>> events({
    String? kind,
    String? unit,
    int? since,
    int? limit,
  }) async {
    eventLoads++;
    return eventsValue
        .where((event) => kind == null || event.kind == kind)
        .toList();
  }

  @override
  Stream<GatewayEvent> stream() => eventsStream.stream;
  @override
  void close() => eventsStream.close();
  @override
  Future<List<GatewayEvent>> calls({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();
  @override
  Future<GatewayHealth> health() => throw UnimplementedError();
  @override
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) => throw UnimplementedError();
  @override
  Future<void> logOut(String refreshToken) => throw UnimplementedError();
  @override
  Future<List<GatewayEvent>> messages({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();
  @override
  Future<Tokens> refresh(String refreshToken) => throw UnimplementedError();
  @override
  Future<List<RackUnit>> units() => throw UnimplementedError();
}
