import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/errors.dart';
import 'package:rackphone_client/src/data/inbox_controller.dart';

import '../support/fake_gateway.dart';

void main() {
  test('each kind is queried on its own and sorted newest first', () async {
    final gateway = FakeGateway(
      eventsValue: [
        event(1),
        event(3),
        event(2, kind: 'call', direction: 'missed'),
        event(4, kind: 'notification'),
      ],
    );
    final inbox = InboxController(gateway: gateway, unit: unit());
    await inbox.load();

    expect(
      gateway.eventQueries.map((query) => query.kind),
      unorderedEquals(['sms', 'call', 'notification']),
    );
    expect(inbox.messages.map((e) => e.id), [3, 1]);
    expect(inbox.calls.map((e) => e.id), [2]);
    expect(inbox.notifications.map((e) => e.id), [4]);
    expect(inbox.loading, isFalse);
  });

  test(
    'kinds the unit may not report are neither queried nor offered',
    () async {
      final gateway = FakeGateway();
      final inbox = InboxController(
        gateway: gateway,
        unit: unit(capabilities: {'notifications'}),
      );
      await inbox.load();

      expect(gateway.eventQueries.map((query) => query.kind), ['notification']);
      expect(inbox.offers(InboxKind.messages), isFalse);
      expect(inbox.offers(InboxKind.calls), isFalse);
      expect(inbox.offers(InboxKind.notifications), isTrue);
    },
  );

  test('a load failure becomes state instead of throwing', () async {
    final gateway = FakeGateway()..eventsFailure = StateError('offline');
    final inbox = InboxController(gateway: gateway, unit: unit());
    await inbox.load();

    expect(inbox.failure, isA<StateError>());
    expect(inbox.messages, isEmpty);
  });

  test('messages group into threads by address, newest thread first', () async {
    final inbox = InboxController(
      gateway: FakeGateway(
        eventsValue: [
          event(1, address: 'Bank'),
          event(2, address: 'Andrew'),
          event(3, address: 'Bank'),
        ],
      ),
      unit: unit(),
    );
    await inbox.load();

    final threads = inbox.threads;
    expect(threads.map((thread) => thread.address), ['Bank', 'Andrew']);
    expect(threads.first.messages.map((e) => e.id), [3, 1]);
    expect(threads.first.latest.id, 3);
  });

  test('two spellings of one number are one conversation', () async {
    final inbox = InboxController(
      gateway: FakeGateway(
        eventsValue: [
          event(1, address: '89161234567'),
          event(2, address: '+7 916 123-45-67'),
          event(3, address: 'Bank'),
        ],
      ),
      unit: unit(),
    );
    await inbox.load();

    expect(inbox.threads.map((thread) => thread.address), [
      'Bank',
      '+7 916 123-45-67',
    ]);
    expect(inbox.threadWith('89161234567')?.messages.map((e) => e.id), [2, 1]);
    expect(inbox.threadWith('+79990000000'), isNull);
  });

  test('history is seen; what streams in later is not', () async {
    final gateway = FakeGateway(eventsValue: [event(1)]);
    final inbox = InboxController(gateway: gateway, unit: unit())..listen();
    await inbox.load();
    expect(inbox.unseen(InboxKind.messages), 0);

    gateway.add(event(2, address: 'Bank'));
    gateway.add(event(3, address: 'Andrew'));
    await pumpEventQueue();
    expect(inbox.unseen(InboxKind.messages), 2);

    inbox.markThreadSeen(inbox.threads.firstWhere((t) => t.address == 'Bank'));
    expect(inbox.unseen(InboxKind.messages), 1);

    inbox.markSeen(InboxKind.messages);
    expect(inbox.unseen(InboxKind.messages), 0);
  });

  test('only a missed call counts as unseen', () async {
    final gateway = FakeGateway();
    final inbox = InboxController(gateway: gateway, unit: unit())..listen();
    await inbox.load();

    gateway.add(event(1, kind: 'call', direction: 'in'));
    gateway.add(event(2, kind: 'call', direction: 'missed'));
    await pumpEventQueue();

    expect(inbox.calls, hasLength(2));
    expect(inbox.unseen(InboxKind.calls), 1);
  });

  test('a ringing call stays out of the log', () async {
    final gateway = FakeGateway();
    final inbox = InboxController(gateway: gateway, unit: unit())..listen();
    await inbox.load();

    gateway.add(event(1, kind: 'call', direction: 'ringing'));
    await pumpEventQueue();

    expect(inbox.calls, isEmpty);
  });

  test('the stream ignores another unit and deduplicates ids', () async {
    final gateway = FakeGateway(eventsValue: [event(1)]);
    final inbox = InboxController(gateway: gateway, unit: unit())..listen();
    await inbox.load();

    gateway.add(event(1));
    gateway.add(event(2, unit: 'other'));
    await pumpEventQueue();

    expect(inbox.messages.map((e) => e.id), [1]);
  });

  test(
    'a stream error keeps events and leaves the tail able to reconnect',
    () async {
      final gateway = FakeGateway(eventsValue: [event(1)]);
      final inbox = InboxController(gateway: gateway, unit: unit())..listen();
      await inbox.load();

      gateway.fail(StateError('dropped'));
      await pumpEventQueue();
      expect(inbox.failure, isA<StateError>());
      expect(inbox.messages.map((e) => e.id), [1]);

      inbox.listen();
      gateway.add(event(2));
      await pumpEventQueue();
      expect(gateway.streams, hasLength(2));
      expect(inbox.messages.map((e) => e.id), [2, 1]);
    },
  );

  test('a dropped stream comes back by itself and fills the gap', () {
    fakeAsync((async) {
      final gateway = FakeGateway(eventsValue: [event(1)]);
      final inbox = InboxController(gateway: gateway, unit: unit())..listen();
      inbox.load();
      async.flushMicrotasks();

      gateway.fail(StateError('dropped'));
      async.flushMicrotasks();
      expect(gateway.streams, hasLength(1));

      gateway.eventsValue = [event(2), event(1)];
      async.elapse(const Duration(seconds: 2));
      expect(gateway.streams, hasLength(2));
      expect(inbox.messages.map((e) => e.id), [2, 1]);

      // A second drop waits longer before trying again.
      gateway.streams.last.close();
      async.elapse(const Duration(seconds: 3));
      expect(gateway.streams, hasLength(2));
      async.elapse(const Duration(seconds: 1));
      expect(gateway.streams, hasLength(3));
      inbox.dispose();
    });
  });

  test('pull to refresh reconnects a stream that is down', () async {
    final gateway = FakeGateway(eventsValue: [event(1)]);
    final inbox = InboxController(gateway: gateway, unit: unit())..listen();
    gateway.fail(StateError('dropped'));
    await pumpEventQueue();

    await inbox.refresh();
    expect(gateway.streams, hasLength(2));
    inbox.dispose();
  });

  test('a signed-out session is not retried', () {
    fakeAsync((async) {
      final gateway = FakeGateway();
      final inbox = InboxController(gateway: gateway, unit: unit())..listen();
      gateway.fail(const GatewayAuthException('expired'));
      async.elapse(const Duration(minutes: 5));
      expect(gateway.streams, hasLength(1));
      inbox.dispose();
    });
  });

  test('a long session keeps the newest 500 of a kind', () async {
    final gateway = FakeGateway(
      eventsValue: [for (var id = 1; id <= 505; id++) event(id)],
    );
    final inbox = InboxController(gateway: gateway, unit: unit());
    await inbox.load();

    expect(inbox.messages, hasLength(500));
    expect(inbox.messages.first.id, 505);
    expect(inbox.messages.last.id, 6);
  });

  test('a reply after dispose reaches no listener', () async {
    final gateway = FakeGateway(eventsValue: [event(1)]);
    final inbox = InboxController(gateway: gateway, unit: unit());
    final loading = inbox.load();
    inbox.dispose();

    await expectLater(loading, completes);
  });

  test(
    'a sent message joins its conversation at once and is not news',
    () async {
      final gateway = FakeGateway(eventsValue: [event(1, address: '+7900')]);
      final inbox = InboxController(gateway: gateway, unit: unit())..listen();
      await inbox.load();

      final sent = await inbox.send('+7900', 'on my way');

      expect(gateway.sent.single, (
        unit: 'lisa01',
        to: '+7900',
        body: 'on my way',
      ));
      expect(inbox.threads.single.latest, sent);
      expect(inbox.unseen(InboxKind.messages), 0);

      // The stream's copy of the same row does not make a second bubble.
      gateway.add(sent);
      await pumpEventQueue();
      expect(inbox.threads.single.messages, hasLength(2));
    },
  );

  test('only a unit that may text can send', () {
    expect(
      InboxController(gateway: FakeGateway(), unit: unit()).canSend,
      isTrue,
    );
    expect(
      InboxController(
        gateway: FakeGateway(),
        unit: unit(capabilities: {'notifications'}),
      ).canSend,
      isFalse,
    );
  });
}
