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
    expect(health.ntfyEnabled, isFalse);
    expect(session.revokedAt, isNull);
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
    });

    expect(stats.eventsByKind, {'sms': 3});
    expect(stats.pushFailed, 2);
  });
}
