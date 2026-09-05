import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/screen/screen_socket.dart';

void main() {
  test(
    'routes channels, ignores unknown channels, and prefixes sends',
    () async {
      final received = Completer<List<int>>();
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        expect(
          request.headers.value(HttpHeaders.authorizationHeader),
          'Bearer token',
        );
        final webSocket = await WebSocketTransformer.upgrade(request);
        webSocket.listen((message) {
          if (!received.isCompleted && message is List<int>) {
            received.complete(message);
          }
        });
        webSocket.add(<int>[9, 90]);
        webSocket.add(<int>[1, 7, 8]);
        webSocket.add(<int>[0, ..._videoStream()]);
      });

      final socket = await ScreenSocket.connect(
        uri: Uri.parse('ws://127.0.0.1:${server.port}/screen'),
        accessToken: 'token',
      );
      final device = await socket.device.first;
      final packet = await socket.video.first;
      final control = await socket.control.first;
      socket.send(<int>[3, 4]);

      expect(device.name, 'phone');
      expect(device.codec, 'h264');
      expect(packet.data, <int>[5, 6]);
      expect(control, <int>[7, 8]);
      expect(await received.future, <int>[1, 3, 4]);
      await socket.close();
      await socket.close();
      await server.close(force: true);
    },
  );

  test('reports a held screen separately from a network failure', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      final webSocket = await WebSocketTransformer.upgrade(request);
      await webSocket.close(
        WebSocketStatus.policyViolation,
        '$sessionBusyReason tablet',
      );
    });

    final socket = await ScreenSocket.connect(
      uri: Uri.parse('ws://127.0.0.1:${server.port}/screen'),
      accessToken: 'token',
    );
    final reason = await socket.closed;
    expect(reason.kind, ScreenCloseKind.heldByAnotherDevice);
    expect(reason.message, contains('tablet'));
    await socket.close();
    await server.close(force: true);
  });
}

List<int> _videoStream() {
  final bytes = Uint8List(77 + 12 + 2);
  bytes.setRange(1, 6, 'phone'.codeUnits);
  bytes.setRange(65, 69, 'h264'.codeUnits);
  final data = ByteData.sublistView(bytes);
  data.setUint32(69, 720, Endian.big);
  data.setUint32(73, 1280, Endian.big);
  data.setUint64(77, 4, Endian.big);
  data.setUint32(85, 2, Endian.big);
  bytes.setRange(89, 91, <int>[5, 6]);
  return bytes;
}
