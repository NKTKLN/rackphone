import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/models.dart';

void main() {
  test('models tolerate missing and null fields', () {
    final tokens = Tokens.fromJson(const {});
    final unit = RackUnit.fromJson(const {'capabilities': null});
    final event = GatewayEvent.fromJson(const {'ts': null});
    final stats = GatewayStats.fromJson(const {'gateway': null});
    final health = GatewayHealth.fromJson(const {});
    final session = Session.fromJson(const {'revoked_at': null});

    expect(tokens.accessToken, isEmpty);
    expect(
      tokens.accessExpiry,
      DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
    );
    expect(unit.capabilities, isEmpty);
    expect(event.timestamp, isNull);
    expect(event.receivedAt, isNull);
    expect(stats.eventsByKind, isEmpty);
    expect(stats.drained, 0);
    expect(stats.security, isNull);
    expect(health.totpEnabled, isFalse);
    expect(event.notify, isTrue);
    expect(session.revokedAt, isNull);
  });

  test('telemetry getters use the exported metric names', () {
    final telemetry = UnitTelemetry.fromJson(const {
      'unit': 'lisa01',
      'up': true,
      'collected_at': 42,
      'samples': {
        'rackphone_battery_capacity_percent': 73,
        'rackphone_battery_temperature_celsius': 31.5,
        'rackphone_temperature_celsius': 34,
        'rackphone_uptime_seconds': 9001,
      },
    });

    expect(telemetry.batteryPercent, 73.0);
    expect(telemetry.batteryTemperature, 31.5);
    expect(telemetry.skinTemperature, 34.0);
    expect(telemetry.uptime, 9001.0);
    expect(() => telemetry.samples['extra'] = 1, throwsUnsupportedError);
  });

  test('value models compare by content and expose capabilities', () {
    final first = RackUnit.fromJson(const {
      'name': 'lisa01',
      'label': 'Top shelf',
      'capabilities': ['sms', 'screen'],
    });
    final second = RackUnit(
      name: 'lisa01',
      label: 'Top shelf',
      capabilities: const {'screen', 'sms'},
    );

    expect(first, second);
    expect(first.hashCode, second.hashCode);
    expect(first.can('screen'), isTrue);
    expect(first.can('files'), isFalse);
  });

  test('stats read store totals and gateway counters', () {
    final stats = GatewayStats.fromJson(const {
      'events': {'sms': 3},
      'gateway': {'drained': 8, 'stored': 7, 'errors': 4},
      'security': {
        'last_login_at': 100,
        'last_login_device': 'console',
        'failed_logins_24h': 2,
        'locked_until': null,
        'totp': 'enabled',
      },
    });

    expect(stats.eventsByKind, {'sms': 3});
    expect(stats.stored, 7);
    expect(stats.security?.lastLoginDevice, 'console');
    expect(stats.security?.totpEnabled, isTrue);
  });

  test('a notification carries its app and title out of raw_json', () {
    final event = GatewayEvent.fromJson(const {
      'id': 3,
      'unit': 'lisa01',
      'kind': 'notification',
      'address': 'org.telegram.messenger',
      'body': 'see you at 7',
      'raw_json': '{"app": "Telegram", "title": "Olga"}',
    });

    expect(event.app, 'Telegram');
    expect(event.title, 'Olga');
  });

  test('a stream frame carries the gateway filter verdict', () {
    final filtered = GatewayEvent.fromJson(const {
      'id': 4,
      'unit': 'lisa01',
      'kind': 'sms',
      'notify': false,
      'filter': 'beeline-app-links',
    });
    final announced = GatewayEvent.fromJson(const {
      'id': 5,
      'unit': 'lisa01',
      'kind': 'sms',
      'notify': true,
    });

    expect(filtered.notify, isFalse);
    expect(announced.notify, isTrue);
    expect(
      filtered ==
          GatewayEvent.fromJson(const {
            'id': 4,
            'unit': 'lisa01',
            'kind': 'sms',
          }),
      isFalse,
    );
  });

  test('a broken raw_json leaves the event readable', () {
    final event = GatewayEvent.fromJson(const {
      'id': 3,
      'unit': 'lisa01',
      'kind': 'sms',
      'raw_json': '{not json',
    });

    expect(event.app, isNull);
    expect(event.kind, 'sms');
  });

  test('the device clock is milliseconds, the host clock seconds', () {
    const both = GatewayEvent(
      id: 1,
      unit: 'u',
      kind: 'sms',
      address: null,
      body: null,
      timestamp: 1700000000123,
      direction: null,
      duration: null,
      receivedAt: 1700000999,
    );
    const hostOnly = GatewayEvent(
      id: 2,
      unit: 'u',
      kind: 'sms',
      address: null,
      body: null,
      timestamp: null,
      direction: null,
      duration: null,
      receivedAt: 1700000999,
    );

    expect(both.occurredAt!.millisecondsSinceEpoch, 1700000000123);
    expect(hostOnly.occurredAt!.millisecondsSinceEpoch, 1700000999000);
  });

  test('the security summary reads TOTP as the gateway words it', () {
    SecuritySummary summary(Map<String, dynamic> totp) =>
        SecuritySummary.fromJson({'failed_logins_24h': 0, ...totp});

    expect(summary({'totp': 'enabled'}).totpEnabled, isTrue);
    expect(summary({'totp': 'disabled'}).totpEnabled, isFalse);
    expect(summary({}).totpEnabled, isFalse);
  });

  test('an event carries the SIM its raw record names', () {
    final event = GatewayEvent.fromJson(<String, dynamic>{
      'id': 1,
      'unit': 'lisa01',
      'kind': 'sms',
      'raw_json': '{"sub":2}',
    });
    expect(event.sub, 2);
    final unknown = GatewayEvent.fromJson(<String, dynamic>{
      'id': 2,
      'unit': 'lisa01',
      'kind': 'sms',
      'raw_json': '{"sub":-1}',
    });
    expect(unknown.sub, isNull);
  });

  test('a SIM list drops entries without an id', () {
    final sims = UnitSims.fromJson(<String, dynamic>{
      'default_sub': 1,
      'sims': [
        {'sub_id': 1, 'slot': 0, 'carrier': 'Beeline'},
        {'slot': 1},
      ],
    });
    expect(sims.defaultSub, 1);
    expect(sims.sims.single.name, 'SIM 1 · Beeline');
  });
}
