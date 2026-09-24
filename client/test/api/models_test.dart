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
    expect(health.ntfyEnabled, isFalse);
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
      'gateway': {
        'drained': 8,
        'stored': 7,
        'filtered': 1,
        'pushed': 6,
        'push_failed': 2,
        'errors': 4,
      },
      'security': {
        'last_login_at': 100,
        'last_login_device': 'console',
        'failed_logins_24h': 2,
        'locked_until': null,
        'totp_enabled': true,
      },
    });

    expect(stats.eventsByKind, {'sms': 3});
    expect(stats.pushFailed, 2);
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
}
