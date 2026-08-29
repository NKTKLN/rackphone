import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_companion/main.dart';
import 'package:rackphone_companion/src/control.dart';
import 'package:rackphone_companion/src/models.dart';

import 'fixtures.dart';

/// A companion that answers without a radio, a SIM or an Android engine.
class FakeControl implements CompanionControl {
  FakeControl({Map<String, dynamic>? status})
    : _status = CompanionStatus.fromJson(status ?? statusJson());

  final CompanionStatus _status;
  final List<String> calls = <String>[];
  Map<String, dynamic> lastConfig = <String, dynamic>{};
  SendRecord? nextKeepaliveResult;
  List<BalanceReading> balances = const <BalanceReading>[];

  @override
  Future<CompanionStatus> status() async {
    calls.add('status');
    return _status;
  }

  @override
  Future<List<SendRecord>> recent({int limit = 20}) async {
    calls.add('recent');
    return <SendRecord>[
      SendRecord.fromJson(<String, dynamic>{
        'id': 'abc',
        'ts': DateTime.now().millisecondsSinceEpoch,
        'to': '+79001234567',
        'source': 'keepalive',
        'status': 'ok',
      }),
    ];
  }

  @override
  Future<String> token() async => 'abcd' * 12;

  @override
  Future<CompanionStatus> setConfig({
    bool? keepaliveEnabled,
    String? keepaliveTo,
    int? keepaliveIntervalHours,
    String? keepaliveBody,
    String? keepaliveSubs,
    bool? collectSms,
    bool? collectCalls,
    bool? includeBody,
    int? subId,
  }) async {
    calls.add('setConfig');
    lastConfig = <String, dynamic>{
      'keepalive_enabled': ?keepaliveEnabled,
      'keepalive_to': ?keepaliveTo,
      'keepalive_interval_hours': ?keepaliveIntervalHours,
      'keepalive_subs': ?keepaliveSubs,
      'collect_sms': ?collectSms,
      'collect_calls': ?collectCalls,
      'include_body': ?includeBody,
      'sub_id': ?subId,
    };
    return _status;
  }

  @override
  Future<SendRecord> send({required String to, required String body}) async {
    calls.add('send');
    return SendRecord.fromJson(<String, dynamic>{
      'id': 'x',
      'to': to,
      'status': 'queued',
      'source': 'ui',
    });
  }

  @override
  Future<SendRecord?> keepaliveNow() async {
    calls.add('keepaliveNow');
    return nextKeepaliveResult;
  }

  @override
  Future<List<BalanceReading>> checkBalance() async {
    calls.add('checkBalance');
    return balances;
  }

  @override
  Future<String> rotateToken() async {
    calls.add('rotateToken');
    return 'ffff' * 12;
  }

  @override
  Future<void> requestPermissions() async => calls.add('requestPermissions');
}

/// The screen is one long scroll of cards, and the default 800x600 test
/// viewport puts most of it off screen. A tall surface keeps the tests about
/// behaviour rather than about scrolling.
Future<void> pumpApp(WidgetTester tester, FakeControl control) async {
  tester.view.physicalSize = const Size(1000, 4000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(CompanionApp(control: control));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('a ready unit says so and lists no blockers', (tester) async {
    final control = FakeControl();

    await pumpApp(tester, control);

    expect(find.text('Ready to send'), findsOneWidget);
    expect(find.text('SEND_SMS is not granted'), findsNothing);
    expect(control.calls, contains('status'));
  });

  testWidgets('a missing permission is named, with the way to fix it', (
    tester,
  ) async {
    final control = FakeControl(
      status: statusJson(ready: false, sendSms: false),
    );

    await pumpApp(tester, control);

    expect(find.text('Not ready'), findsOneWidget);
    expect(find.text('SEND_SMS is not granted'), findsOneWidget);

    await tester.tap(find.text('Grant SMS permission'));
    await tester.pumpAndSettle();
    expect(control.calls, contains('requestPermissions'));
  });

  testWidgets('the compose button is dead while the app cannot send', (
    tester,
  ) async {
    final control = FakeControl(
      status: statusJson(ready: false, sendSms: false),
    );

    await pumpApp(tester, control);

    final button = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Compose'),
    );
    expect(button.onPressed, isNull);
  });

  testWidgets('saving the schedule sends the edited values', (tester) async {
    final control = FakeControl();

    await pumpApp(tester, control);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Send to'),
      '+79005550101',
    );
    await tester.enterText(find.widgetWithText(TextFormField, 'Every'), '168');
    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();

    expect(control.lastConfig['keepalive_to'], '+79005550101');
    expect(control.lastConfig['keepalive_interval_hours'], 168);
  });

  testWidgets('an unusable target is refused before it reaches the unit', (
    tester,
  ) async {
    final control = FakeControl();

    await pumpApp(tester, control);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Send to'),
      'my phone',
    );
    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();

    expect(find.text('Use a phone number, or "self"'), findsOneWidget);
    expect(control.calls, isNot(contains('setConfig')));
  });

  testWidgets('an interval preset fills the field', (tester) async {
    final control = FakeControl();

    await pumpApp(tester, control);
    await tester.tap(find.widgetWithText(ActionChip, 'every 7 days'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();

    expect(control.lastConfig['keepalive_interval_hours'], 168);
  });

  testWidgets('send now reports that nothing was due', (tester) async {
    final control = FakeControl();

    await pumpApp(tester, control);
    await tester.tap(find.widgetWithText(OutlinedButton, 'Send now'));
    await tester.pumpAndSettle();

    expect(control.calls, contains('keepaliveNow'));
    expect(find.text('Nothing was due'), findsOneWidget);
  });

  testWidgets('both SIMs get their own keepalive line', (tester) async {
    final control = FakeControl();

    await pumpApp(tester, control);

    // Named the same way the SIM picker names them, so the two lists read as
    // the same device rather than as two views of unrelated numbers.
    expect(find.text('SIM 1 · beeline'), findsWidgets);
    expect(find.text('SIM 2 · Yota'), findsWidgets);
  });

  testWidgets('a SIM that cannot text itself says so', (tester) async {
    final control = FakeControl(status: statusJson(secondSimResolves: false));

    await pumpApp(tester, control);

    expect(find.text('no number to text'), findsOneWidget);
    expect(
      find.textContaining('does not carry its own number'),
      findsOneWidget,
    );
  });

  testWidgets('the keepalive can be narrowed to the default SIM', (
    tester,
  ) async {
    final control = FakeControl();

    await pumpApp(tester, control);
    await tester.tap(find.text('Default SIM').last);
    await tester.pumpAndSettle();

    expect(control.lastConfig['keepalive_subs'], 'default');
  });

  testWidgets('collection can be turned off without touching the rest', (
    tester,
  ) async {
    final control = FakeControl();

    await pumpApp(tester, control);
    await tester.tap(find.widgetWithText(SwitchListTile, 'Collect SMS'));
    await tester.pumpAndSettle();

    expect(control.lastConfig, <String, dynamic>{'collect_sms': false});
  });

  testWidgets('a spool the host is not draining is visible', (tester) async {
    final control = FakeControl(status: statusJson(pending: 12, dropped: 3));

    await pumpApp(tester, control);

    expect(find.text('12'), findsOneWidget);
    expect(find.text('Dropped at the 2000 cap'), findsOneWidget);
  });

  testWidgets('a known balance is shown next to the SIM that reported it', (
    tester,
  ) async {
    final control = FakeControl(status: statusJson(balance: 42.5));

    await pumpApp(tester, control);

    expect(find.textContaining('42.50'), findsOneWidget);
  });

  testWidgets('checking the balance reports what came back', (tester) async {
    final control = FakeControl()
      ..balances = <BalanceReading>[
        const BalanceReading(
          subId: 1,
          amount: 12.3,
          text: 'Balans: 12.30 r',
          checkedMs: 1700000000000,
        ),
      ];

    await pumpApp(tester, control);
    await tester.tap(find.widgetWithText(OutlinedButton, 'Check balance'));
    await tester.pumpAndSettle();

    expect(control.calls, contains('checkBalance'));
    expect(find.text('12.30'), findsOneWidget);
  });

  testWidgets('the token is masked until it is asked for', (tester) async {
    final control = FakeControl();

    await pumpApp(tester, control);

    expect(find.text('abcd' * 12), findsNothing);
    await tester.tap(find.text('Reveal'));
    await tester.pumpAndSettle();
    expect(find.text('abcd' * 12), findsOneWidget);
  });
}
