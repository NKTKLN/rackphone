import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/call/call_socket.dart';

void main() {
  test('parses the header and relays frames across arbitrary chunks', () async {
    final received = Completer<List<int>>();
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      expect(
        request.headers.value(HttpHeaders.authorizationHeader),
        'Bearer token',
      );
      final socket = await WebSocketTransformer.upgrade(request);
      socket.listen((message) {
        if (!received.isCompleted && message is List<int>) {
          received.complete(message);
        }
      });
      final stream = _audioStream();
      socket
        ..add(stream.sublist(0, 3))
        ..add(stream.sublist(3, 10))
        ..add(stream.sublist(10, 15))
        ..add(stream.sublist(15));
    });

    final socket = await CallAudioSocket.connect(
      uri: Uri.parse('ws://127.0.0.1:${server.port}/call/audio'),
      accessToken: 'token',
    );
    final frames = socket.downlink.take(2).toList();
    final format = await socket.ready;
    socket.sendUplink(Uint8List.fromList(<int>[9, 8, 7, 6]));

    expect(format.sampleRate, 16000);
    expect(format.frameBytes, 4);
    expect(await frames, <List<int>>[
      <int>[1, 2, 3, 4],
      <int>[5, 6, 7, 8],
    ]);
    expect(await received.future, <int>[9, 8, 7, 6]);
    await socket.close();
    await server.close(force: true);
  });

  test('reports a busy audio session separately', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      final socket = await WebSocketTransformer.upgrade(request);
      await socket.close(
        WebSocketStatus.policyViolation,
        '$callSessionBusyReason Desk tablet',
      );
    });
    final socket = await CallAudioSocket.connect(
      uri: Uri.parse('ws://127.0.0.1:${server.port}/call/audio'),
      accessToken: 'token',
    );

    final reason = await socket.closed;

    expect(reason.kind, CallCloseKind.heldByAnotherDevice);
    expect(reason.holder, 'Desk tablet');
    await socket.close();
    await server.close(force: true);
  });
}

List<int> _audioStream() {
  final bytes = Uint8List(16);
  final data = ByteData.sublistView(bytes);
  data
    ..setUint32(0, 16000, Endian.big)
    ..setUint32(4, 4, Endian.big);
  bytes.setRange(8, 16, <int>[1, 2, 3, 4, 5, 6, 7, 8]);
  return bytes;
}
