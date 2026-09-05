import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/errors.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/data/feed_controller.dart';

void main() {
  test('load populates events and clears loading', () async {
    final gateway = _FakeGateway()..queried = [_event(2), _event(1)];
    final controller = FeedController(gateway: gateway, unit: 'lisa01');

    final loading = <bool>[];
    controller.addListener(() => loading.add(controller.loading));
    await controller.load(kind: 'sms');

    expect(loading, containsAllInOrder([true, false]));
    expect(controller.events.map((event) => event.id), [2, 1]);
    expect(gateway.lastKind, 'sms');
  });

  test('load failure becomes state instead of throwing', () async {
    final failure = StateError('offline');
    final gateway = _FakeGateway()..queryFailure = failure;
    final controller = FeedController(gateway: gateway, unit: 'lisa01');

    await controller.load();

    expect(controller.failure, same(failure));
    expect(controller.loading, isFalse);
  });

  test('stream ignores another unit and deduplicates ids', () async {
    final gateway = _FakeGateway();
    final controller = FeedController(gateway: gateway, unit: 'lisa01');
    controller.listen();

    gateway.add(_event(1, unit: 'other'));
    gateway.add(_event(2));
    gateway.add(_event(2));
    await Future<void>.delayed(Duration.zero);

    expect(controller.events.map((event) => event.id), [2]);
    await controller.stop();
  });

  test('stream error keeps existing events', () async {
    final gateway = _FakeGateway()..queried = [_event(1)];
    final controller = FeedController(gateway: gateway, unit: 'lisa01');
    await controller.load();
    controller.listen();

    final failure = StateError('dropped');
    gateway.fail(failure);
    await Future<void>.delayed(Duration.zero);

    expect(controller.failure, same(failure));
    expect(controller.events.map((event) => event.id), [1]);
    await controller.stop();
  });

  test('cap keeps newest 500 and drops the oldest', () async {
    final gateway = _FakeGateway()
      ..queried = List.generate(501, (index) => _event(501 - index));
    final controller = FeedController(gateway: gateway, unit: 'lisa01');

    await controller.load();

    expect(controller.events, hasLength(500));
    expect(controller.events.first.id, 501);
    expect(controller.events.last.id, 2);
  });

  test('a stream error leaves the tail able to reconnect', () async {
    // A subscription kept after its stream ended turns every later listen into
    // a silent no-op, and the feed stops updating for good.
    final gateway = _FakeGateway();
    final controller = FeedController(gateway: gateway, unit: 'lisa01');
    controller.listen();
    gateway.fail(const GatewayNetworkException('dropped'));
    await Future<void>.delayed(Duration.zero);

    controller.listen();
    gateway.add(_event(1));
    await Future<void>.delayed(Duration.zero);

    expect(controller.events, hasLength(1));
  });
}

GatewayEvent _event(int id, {String unit = 'lisa01'}) => GatewayEvent(
  id: id,
  unit: unit,
  kind: 'sms',
  address: null,
  body: null,
  timestamp: id,
  direction: null,
  duration: null,
  receivedAt: id,
);

final class _FakeGateway implements GatewayApi {
  @override
  Future<UnitTelemetry> telemetry(String unit) => throw UnimplementedError();

  // A fresh controller per call, because every real `stream()` is a new HTTP
  // request - one single-subscription stream handed out twice cannot be
  // listened to again after a cancel, which is exactly what a reconnect does.
  final List<StreamController<GatewayEvent>> _streams = [];
  List<GatewayEvent> queried = const [];
  Object? queryFailure;
  String? lastKind;

  void add(GatewayEvent event) => _streams.last.add(event);
  void fail(Object failure) => _streams.last.addError(failure);

  @override
  Future<List<GatewayEvent>> events({
    String? kind,
    String? unit,
    int? since,
    int? limit,
  }) async {
    lastKind = kind;
    final failure = queryFailure;
    if (failure != null) throw failure;
    return queried;
  }

  @override
  Stream<GatewayEvent> stream() {
    final controller = StreamController<GatewayEvent>();
    _streams.add(controller);
    return controller.stream;
  }

  @override
  void close() {
    for (final controller in _streams) {
      controller.close();
    }
  }

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
  Future<GatewayStats> stats() => throw UnimplementedError();

  @override
  Future<List<RackUnit>> units() => throw UnimplementedError();
}
