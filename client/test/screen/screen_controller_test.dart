import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/screen/decoder.dart';
import 'package:rackphone_client/src/screen/protocol.dart';
import 'package:rackphone_client/src/screen/screen_controller.dart';
import 'package:rackphone_client/src/screen/screen_socket.dart';

void main() {
  test('maps both axes through letterboxing into device pixels', () async {
    final connection = _FakeConnection();
    final controller = ScreenController(
      socketFactory: () async => connection,
      decoder: FakeScreenDecoder(),
    );
    await controller.connect();
    connection.devices.add(_device);
    await _flush();

    controller.sendTouch(0, const Offset(500, 375), const Size(1000, 1000));
    controller.sendTouch(2, const Offset(500, 125), const Size(1000, 250));
    final verticallyBoxed = ByteData.sublistView(
      Uint8List.fromList(connection.sent.first),
    );
    final horizontallyBoxed = ByteData.sublistView(
      Uint8List.fromList(connection.sent.last),
    );
    expect(verticallyBoxed.getUint32(10, Endian.big), 100);
    expect(verticallyBoxed.getUint32(14, Endian.big), 25);
    expect(horizontallyBoxed.getUint32(10, Endian.big), 100);
    expect(horizontallyBoxed.getUint32(14, Endian.big), 50);
  });

  test(
    'feeds packets in order after configuring from only the first device',
    () async {
      final connection = _FakeConnection();
      final decoder = FakeScreenDecoder();
      final controller = ScreenController(
        socketFactory: () async => connection,
        decoder: decoder,
      );
      await controller.connect();
      connection.devices
        ..add(_device)
        ..add(
          const DeviceInfo(name: 'later', codec: 'h264', width: 50, height: 50),
        );
      connection.packets
        ..add(_packet(1))
        ..add(_packet(2));
      await _flush();

      expect(decoder.calls.whereType<ConfigureDecoderCall>(), hasLength(1));
      expect(
        decoder.calls.whereType<FeedDecoderCall>().map(
          (call) => call.packet.pts,
        ),
        [1, 2],
      );
    },
  );

  test(
    'a refusal names the holder and stays out of the failure branch',
    () async {
      // Waiting for someone to release a screen and reconnecting after a dropped
      // network are different actions, so they must not share a state.
      final connection = _FakeConnection();
      final controller = ScreenController(
        socketFactory: () async => connection,
        decoder: FakeScreenDecoder(),
      );
      await controller.connect();
      connection.closedCompleter.complete(
        const ScreenCloseReason(
          ScreenCloseKind.heldByAnotherDevice,
          '$sessionBusyReason Desk tablet',
        ),
      );
      await _flush();

      expect(controller.state, ScreenState.heldByAnotherDevice);
      expect(controller.holder, 'Desk tablet');
    },
  );

  test('a rotation the decoder reports remaps touches at once', () async {
    final connection = _FakeConnection();
    final decoder = FakeScreenDecoder();
    final controller = ScreenController(
      socketFactory: () async => connection,
      decoder: decoder,
    );
    await controller.connect();
    connection.devices.add(_device);
    await _flush();

    decoder.reportSize(_device.height, _device.width);

    expect(controller.device!.width, _device.height);
    expect(controller.device!.height, _device.width);
    controller.sendTouch(0, Offset.zero, const Size(100, 200));
    final touch = ByteData.sublistView(
      Uint8List.fromList(connection.sent.last),
    );
    expect(touch.getUint16(18), _device.height, reason: 'screen width');
    expect(touch.getUint16(20), _device.width, reason: 'screen height');
  });

  test('disconnect closes transport and decoder only once', () async {
    final connection = _FakeConnection();
    final decoder = FakeScreenDecoder();
    final controller = ScreenController(
      socketFactory: () async => connection,
      decoder: decoder,
    );
    await controller.connect();
    await controller.disconnect();
    await controller.disconnect();

    expect(connection.closeCalls, 1);
    expect(decoder.calls.whereType<DisposeDecoderCall>(), hasLength(1));
  });
}

const _device = DeviceInfo(
  name: 'phone',
  codec: 'h264',
  width: 200,
  height: 100,
);

VideoPacket _packet(int pts) =>
    VideoPacket(pts: pts, isConfig: false, isKeyFrame: false, data: [pts]);

Future<void> _flush() async {
  await Future<void>.delayed(Duration.zero);
  await Future<void>.delayed(Duration.zero);
}

final class _FakeConnection implements ScreenConnection {
  final devices = StreamController<DeviceInfo>();
  final packets = StreamController<VideoPacket>();
  final closedCompleter = Completer<ScreenCloseReason>();
  final List<List<int>> sent = [];
  int closeCalls = 0;

  @override
  Future<ScreenCloseReason> get closed => closedCompleter.future;
  @override
  Stream<DeviceInfo> get device => devices.stream;
  @override
  Stream<VideoPacket> get video => packets.stream;
  @override
  void send(List<int> controlMessage) => sent.add(controlMessage);
  @override
  Future<void> close() async {
    closeCalls++;
  }
}
