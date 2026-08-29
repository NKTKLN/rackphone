import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_companion/src/models.dart';

import 'fixtures.dart';

void main() {
  group('CompanionStatus', () {
    test('parses the document the host also reads', () {
      final status = CompanionStatus.fromJson(statusJson());

      expect(status.ready, isTrue);
      expect(status.blockers, isEmpty);
      expect(status.sims.first.title, 'SIM 1 · beeline');
      expect(status.sims.last.title, 'SIM 2 · Yota');
      expect(status.keepalive.intervalHours, 720);
      expect(status.keepalive.isSelf, isTrue);
      expect(status.counters.sentFailed, 1);
      expect(status.lastSend?.source, 'keepalive');
    });

    test('names what is blocking a unit that cannot send', () {
      final status = CompanionStatus.fromJson(
        statusJson(ready: false, sendSms: false),
      );

      expect(status.ready, isFalse);
      expect(status.blockers, contains('SEND_SMS is not granted'));
    });

    test('survives a status document with fields missing', () {
      final status = CompanionStatus.fromJson(<String, dynamic>{});

      expect(status.ready, isFalse);
      expect(status.sims, isEmpty);
      expect(status.lastSend, isNull);
      expect(status.keepalive.intervalHours, 720);
      expect(status.keepalive.targets, isEmpty);
      expect(status.inbox.pending, 0);
    });

    test('every SIM gets its own keepalive clock', () {
      final status = CompanionStatus.fromJson(statusJson());
      final targets = status.keepalive.targets;

      expect(targets, hasLength(2));
      expect(targets.first.subId, 1);
      expect(targets.first.lastSuccessMs, greaterThan(0));
      // The second SIM has never sent, and says so rather than inheriting the
      // first one's timestamp.
      expect(targets.last.subId, 2);
      expect(targets.last.lastSuccessMs, 0);
      expect(status.keepalive.coversAllSims, isTrue);
    });

    test('a SIM that cannot text itself is named, not averaged away', () {
      final status = CompanionStatus.fromJson(
        statusJson(secondSimResolves: false),
      );

      expect(status.keepalive.unresolved, hasLength(1));
      expect(status.keepalive.unresolved.single.subId, 2);
    });

    test('narrowing to the default SIM is visible in the status', () {
      final status = CompanionStatus.fromJson(statusJson(subs: 'default'));
      expect(status.keepalive.coversAllSims, isFalse);
    });

    test('a balance is attached to the SIM that reported it', () {
      final status = CompanionStatus.fromJson(statusJson(balance: -57));

      expect(status.balances, hasLength(1));
      expect(status.balanceFor(1)?.amount, -57);
      // A SIM that has never answered has no reading, rather than a zero that
      // would look like an empty balance.
      expect(status.balanceFor(3), isNull);
      expect(status.balanceFor(1)?.label, '-57.00');
    });

    test('a reply with no number keeps the operator words', () {
      final reading = BalanceReading.fromJson(<String, dynamic>{
        'sub_id': 1,
        'text': 'Услуга временно недоступна',
        'checked_ms': 1,
      });

      expect(reading.amount, isNull);
      expect(reading.label, 'Услуга временно недоступна');
    });

    test('the inbox reports what is waiting and what was lost', () {
      final status = CompanionStatus.fromJson(
        statusJson(pending: 12, dropped: 3, collectSms: false),
      );

      expect(status.inbox.pending, 12);
      expect(status.inbox.dropped, 3);
      expect(status.inbox.collectSms, isFalse);
      expect(status.inbox.collectCalls, isTrue);
    });

    test('a deferred alarm is a blocker, not a detail', () {
      // The unit can send; it just will not be woken up to. That reads as
      // healthy everywhere else, which is why it is named here.
      final status = CompanionStatus.fromJson(
        statusJson(ready: false, batteryExempt: false),
      );

      expect(
        status.blockers,
        contains('Battery optimisation defers the keepalive alarm'),
      );
      expect(status.standbyBucket, 'restricted');
    });

    test('a unit that cannot receive is not ready to relay anything', () {
      final status = CompanionStatus.fromJson(
        statusJson(ready: false, receiveSms: false),
      );

      expect(status.blockers, contains('RECEIVE_SMS is not granted'));
    });
  });

  group('validateTarget', () {
    test('accepts numbers, formatted or not, and short codes', () {
      expect(validateTarget('+79001234567'), isNull);
      expect(validateTarget('+7 (900) 123-45-67'), isNull);
      expect(validateTarget('900'), isNull);
    });

    test('accepts self only where self means something', () {
      expect(validateTarget('self'), isNull);
      expect(validateTarget('self', allowSelf: false), isNotNull);
      expect(validateTarget('', allowSelf: false), isNotNull);
    });

    test('rejects anything the radio would refuse', () {
      expect(validateTarget('call me'), isNotNull);
      expect(validateTarget('+7900+123'), isNotNull);
      expect(validateTarget('1' * 21), isNotNull);
    });
  });

  group('validateIntervalHours', () {
    test('accepts the declared range', () {
      expect(validateIntervalHours('1'), isNull);
      expect(validateIntervalHours('720'), isNull);
      expect(validateIntervalHours('8760'), isNull);
    });

    test('rejects zero, negatives, a year and a half, and prose', () {
      expect(validateIntervalHours('0'), isNotNull);
      expect(validateIntervalHours('-5'), isNotNull);
      expect(validateIntervalHours('9000'), isNotNull);
      expect(validateIntervalHours('weekly'), isNotNull);
    });
  });

  group('formatting', () {
    test('describes an interval the way it was meant', () {
      expect(describeInterval(24), 'every day');
      expect(describeInterval(168), 'every 7 days');
      expect(describeInterval(720), 'every 30 days');
      expect(describeInterval(1), 'every hour');
      expect(describeInterval(5), 'every 5 hours');
    });

    test('describes a moment as a distance, in both directions', () {
      final now = DateTime(2026, 8, 27, 12);
      int at(Duration offset) => now.add(offset).millisecondsSinceEpoch;

      expect(describeWhen(null, now: now), 'never');
      expect(describeWhen(0, now: now), 'never');
      expect(
        describeWhen(at(const Duration(days: 30)), now: now),
        'in 30 days',
      );
      expect(describeWhen(at(const Duration(hours: -3)), now: now), '3 h ago');
      expect(
        describeWhen(at(const Duration(minutes: 20)), now: now),
        'in 20 min',
      );
      expect(
        describeWhen(at(const Duration(seconds: 5)), now: now),
        'in moments',
      );
    });
  });
}
