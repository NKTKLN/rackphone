import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_companion/src/control.dart';
import 'package:rackphone_companion/src/models.dart';

import 'fixtures.dart';

/// The channel is the seam between Dart and the only code that can send an
/// SMS, so what crosses it is worth pinning down: a renamed argument fails
/// silently on the native side, where an absent key means "leave unchanged".
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  const channel = MethodChannel('com.nktkln.rackphone.companion/control');
  final calls = <MethodCall>[];

  setUp(() {
    calls.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (MethodCall call) async {
          calls.add(call);
          return switch (call.method) {
            'status' || 'setConfig' => jsonEncode(statusJson()),
            'send' => jsonEncode(<String, dynamic>{
              'id': 'x',
              'to': '+79001234567',
              'status': 'queued',
            }),
            'keepaliveNow' => jsonEncode(<String, dynamic>{
              'status': 'not_due',
            }),
            'recent' => jsonEncode(<dynamic>[
              <String, dynamic>{'id': 'a', 'status': 'ok', 'to': '900'},
            ]),
            'token' || 'rotateToken' => 'deadbeef',
            _ => null,
          };
        });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  test('status decodes the native JSON document', () async {
    final status = await const MethodChannelControl().status();

    expect(status.ready, isTrue);
    expect(status.sims.map((SimInfo sim) => sim.subId), <int>[1, 2]);
    expect(status.keepalive.targets, hasLength(2));
  });

  test('setConfig sends only the keys it was given', () async {
    await const MethodChannelControl().setConfig(keepaliveEnabled: true);

    final args = Map<String, dynamic>.from(calls.single.arguments as Map);
    expect(args, <String, dynamic>{'keepalive_enabled': true});
  });

  test('setConfig uses the same key names as the CONFIG broadcast', () async {
    await const MethodChannelControl().setConfig(
      keepaliveTo: 'self',
      keepaliveIntervalHours: 720,
      keepaliveBody: 'rackphone keepalive',
      keepaliveSubs: 'all',
      subId: 2,
    );

    final args = Map<String, dynamic>.from(calls.single.arguments as Map);
    expect(args.keys, <String>[
      'keepalive_to',
      'keepalive_interval_hours',
      'keepalive_body',
      'keepalive_subs',
      'sub_id',
    ]);
  });

  test(
    'a keepalive that was not due reports nothing rather than a record',
    () async {
      expect(await const MethodChannelControl().keepaliveNow(), isNull);
    },
  );

  test('recent decodes the outbox tail', () async {
    final records = await const MethodChannelControl().recent(limit: 5);

    expect(records.single.to, '900');
    expect(calls.single.arguments, <String, dynamic>{'limit': 5});
  });

  test('send passes the destination through untouched', () async {
    await const MethodChannelControl().send(to: '+7 900 123-45-67', body: 'hi');

    // Sanitising happens once, natively, next to the radio that has the final
    // say about what it will accept.
    expect(calls.single.arguments, <String, dynamic>{
      'to': '+7 900 123-45-67',
      'body': 'hi',
    });
  });
}
