import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/call/audio.dart';
import 'package:rackphone_client/src/call/call_controller.dart';
import 'package:rackphone_client/src/call/call_socket.dart';
import 'package:rackphone_client/src/call/protocol.dart';

void main() {
  test(
    'ringing, accept, mute, and remote end follow the call lifecycle',
    () async {
      final events = StreamController<GatewayEvent>();
      final gateway = _FakeCallsApi();
      final connection = _FakeConnection();
      final audio = FakeCallAudio();
      final controller = CallController(
        gateway: gateway,
        events: events.stream,
        audio: audio,
        socketFactory: (_) async => connection,
      );
      events.add(_event(direction: 'ringing', address: '+15551234'));
      await _flush();
      expect(controller.state, CallState.ringing);
      expect(controller.caller, '+15551234');

      final accepting = controller.accept();
      await _flush();
      expect(controller.state, CallState.connecting);
      connection.format.complete(
        const CallAudioFormat(sampleRate: 16000, frameBytes: 4),
      );
      await accepting;
      expect(controller.state, CallState.inCall);
      expect(gateway.answered, <String>['unit-1']);

      audio.captured.add(Uint8List.fromList(<int>[1, 2, 3, 4]));
      await _flush();
      controller.toggleMute();
      audio.captured.add(Uint8List.fromList(<int>[5, 6, 7, 8]));
      await _flush();
      expect(connection.sent, <List<int>>[
        <int>[1, 2, 3, 4],
      ]);

      events.add(_event(direction: 'answered', address: '+15551234'));
      await _flush();
      expect(controller.state, CallState.ended);
      expect(connection.closeCalls, 1);
      controller.dispose();
    },
  );

  test(
    'reject controls the gateway and an unknown caller is retained',
    () async {
      final events = StreamController<GatewayEvent>();
      final gateway = _FakeCallsApi();
      final controller = CallController(
        gateway: gateway,
        events: events.stream,
        audio: FakeCallAudio(),
        socketFactory: (_) async => _FakeConnection(),
      );
      events.add(_event(direction: 'ringing', address: ''));
      await _flush();
      expect(controller.caller, isNull);

      await controller.reject();

      expect(gateway.rejected, <String>['unit-1']);
      expect(controller.state, CallState.ended);
      controller.dispose();
    },
  );

  test(
    'a placed call bridges audio, takes keys, and hangs up on the unit',
    () async {
      final events = StreamController<GatewayEvent>();
      final gateway = _FakeCallsApi();
      final connection = _FakeConnection();
      final controller = CallController(
        gateway: gateway,
        events: events.stream,
        audio: FakeCallAudio(),
        socketFactory: (_) async => connection,
      );

      final dialling = controller.dial('unit-1', '+7900');
      await _flush();
      expect(controller.state, CallState.connecting);
      expect(controller.outgoing, isTrue);
      expect(gateway.dialled, ['unit-1 +7900']);
      connection.format.complete(
        const CallAudioFormat(sampleRate: 16000, frameBytes: 4),
      );
      await dialling;
      expect(controller.state, CallState.inCall);

      await controller.press('1');
      await controller.press('#');
      expect(gateway.pressed, ['1', '#']);
      expect(controller.keys, '1#');

      await controller.hangup();
      expect(gateway.ended, ['unit-1']);
      expect(controller.state, CallState.ended);
      expect(connection.closeCalls, 1);
      controller.dispose();
    },
  );

  test(
    'the unit logging a placed call ends it here, however it spelled the number',
    () async {
      final events = StreamController<GatewayEvent>();
      final connection = _FakeConnection();
      final controller = CallController(
        gateway: _FakeCallsApi(),
        events: events.stream,
        audio: FakeCallAudio(),
        socketFactory: (_) async => connection,
      );
      final dialling = controller.dial('unit-1', '8 900');
      await _flush();
      connection.format.complete(
        const CallAudioFormat(sampleRate: 16000, frameBytes: 4),
      );
      await dialling;

      // Another unit's call and a ringing event are not this call ending.
      events.add(_eventOn('unit-2', direction: 'out', address: '+7900'));
      events.add(_event(direction: 'ringing', address: '+7111'));
      await _flush();
      expect(controller.state, CallState.inCall);

      events.add(_event(direction: 'out', address: '+7900'));
      await _flush();
      expect(controller.state, CallState.ended);
      controller.dispose();
    },
  );

  test('a late record of the previous call does not end this one', () async {
    final events = StreamController<GatewayEvent>();
    final connection = _FakeConnection();
    final controller = CallController(
      gateway: _FakeCallsApi()..placedAt = 5000,
      events: events.stream,
      audio: FakeCallAudio(),
      socketFactory: (_) async => connection,
    );
    final dialling = controller.dial('unit-1', '+7900');
    await _flush();
    connection.format.complete(
      const CallAudioFormat(sampleRate: 16000, frameBytes: 4),
    );
    await dialling;

    events.add(_event(direction: 'out', address: '+7111', timestamp: 4000));
    await _flush();
    expect(controller.state, CallState.inCall);

    events.add(_event(direction: 'out', address: '+7900', timestamp: 5100));
    await _flush();
    expect(controller.state, CallState.ended);
    controller.dispose();
  });

  test('a refused dial ends with the reason', () async {
    final gateway = _FakeCallsApi()..dialFailure = StateError('busy');
    final controller = CallController(
      gateway: gateway,
      events: const Stream.empty(),
      audio: FakeCallAudio(),
      socketFactory: (_) async => _FakeConnection(),
    );

    await controller.dial('unit-1', '+7900');

    expect(controller.state, CallState.ended);
    expect(controller.message, contains('busy'));
    controller.dispose();
  });
}

GatewayEvent _event({
  required String direction,
  required String address,
  int? timestamp,
}) => _eventOn(
  'unit-1',
  direction: direction,
  address: address,
  timestamp: timestamp,
);

GatewayEvent _eventOn(
  String unit, {
  required String direction,
  required String address,
  int? timestamp,
}) => GatewayEvent(
  id: 1,
  unit: unit,
  kind: 'call',
  address: address,
  body: null,
  timestamp: timestamp,
  direction: direction,
  duration: null,
  receivedAt: null,
);

Future<void> _flush() async {
  await Future<void>.delayed(Duration.zero);
  await Future<void>.delayed(Duration.zero);
}

final class _FakeCallsApi implements GatewayCallsApi {
  final List<String> answered = <String>[];
  final List<String> rejected = <String>[];
  final List<String> dialled = <String>[];
  final List<String> ended = <String>[];
  final List<String> pressed = <String>[];
  Object? dialFailure;
  int? placedAt;

  @override
  Future<CallActionResult> dial(String unit, String to) async {
    final failure = dialFailure;
    if (failure != null) throw failure;
    dialled.add('$unit $to');
    return CallActionResult(
      status: 'dialing',
      accepted: true,
      placedAt: placedAt,
    );
  }

  @override
  Future<CallActionResult> endCall(String unit) async {
    ended.add(unit);
    return const CallActionResult(status: 'ended', accepted: true);
  }

  @override
  Future<CallActionResult> sendDtmf(String unit, String digits) async {
    pressed.add(digits);
    return const CallActionResult(status: 'sent', accepted: true);
  }

  @override
  Future<CallActionResult> answerCall(String unit) async {
    answered.add(unit);
    return const CallActionResult(status: 'answered', accepted: true);
  }

  @override
  Future<CallActionResult> rejectCall(String unit) async {
    rejected.add(unit);
    return const CallActionResult(status: 'rejected', accepted: true);
  }

  @override
  Future<CallAudioSocket> callAudio(String unit) => throw UnimplementedError();
}

final class _FakeConnection implements CallAudioConnection {
  final format = Completer<CallAudioFormat>();
  final frames = StreamController<Uint8List>();
  final finished = Completer<CallCloseReason>();
  final List<Uint8List> sent = <Uint8List>[];
  int closeCalls = 0;

  @override
  Future<CallCloseReason> get closed => finished.future;
  @override
  Stream<Uint8List> get downlink => frames.stream;
  @override
  Future<CallAudioFormat> get ready => format.future;
  @override
  Future<void> close() async => closeCalls++;
  @override
  void sendUplink(Uint8List frame) => sent.add(Uint8List.fromList(frame));
}
