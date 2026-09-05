import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/screen/protocol.dart';

void main() {
  test(
    'parses introductions and packets across arbitrary boundaries',
    () async {
      final parser = VideoStreamParser();
      final devices = <DeviceInfo>[];
      final packets = <VideoPacket>[];
      parser.device.listen(devices.add);
      parser.video.listen(packets.add);

      final introduction = _introduction('rack-phone', 'h264', 1080, 1920);
      parser.add(introduction.sublist(0, 1));
      parser.add(introduction.sublist(1, 12));
      parser.add(introduction.sublist(12, 43));
      parser.add(introduction.sublist(43));

      final first = _packet(42, isKeyFrame: true, data: <int>[1, 2, 3]);
      parser.add(first.sublist(0, 6));
      parser.add(first.sublist(6, 10));
      parser.add(first.sublist(10, 12));
      parser.add(first.sublist(12, 14));
      parser.add(first.sublist(14));

      final second = _packet(0, isConfig: true, data: <int>[4, 5]);
      final third = _packet(99, data: <int>[6]);
      parser.add(<int>[...second, ...third]);
      await parser.close();

      expect(devices, hasLength(1));
      expect(devices.single.name, 'rack-phone');
      expect(devices.single.codec, 'h264');
      expect(devices.single.width, 1080);
      expect(devices.single.height, 1920);
      expect(packets, hasLength(3));
      expect(packets[0].pts, 42);
      expect(packets[0].isConfig, isFalse);
      expect(packets[0].isKeyFrame, isTrue);
      expect(packets[0].data, <int>[1, 2, 3]);
      expect(packets[1].isConfig, isTrue);
      expect(packets[1].data, <int>[4, 5]);
      expect(packets[2].pts, 99);
      expect(packets[2].data, <int>[6]);
    },
  );

  test('encodes a touch event byte for byte', () {
    expect(
      encodeTouchEvent(
        action: 2,
        pointerId: 0x0102030405060708,
        x: 0x11121314,
        y: 0x21222324,
        screenWidth: 0x3132,
        screenHeight: 0x4142,
        pressure: 0.5,
        buttons: 0x51525354,
      ),
      <int>[
        2,
        2,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        0x11,
        0x12,
        0x13,
        0x14,
        0x21,
        0x22,
        0x23,
        0x24,
        0x31,
        0x32,
        0x41,
        0x42,
        0x80,
        0,
        0,
        0,
        0,
        0,
        0x51,
        0x52,
        0x53,
        0x54,
      ],
    );
  });

  test('encodes a key event byte for byte', () {
    expect(
      encodeKeyEvent(
        action: 1,
        keycode: 0x01020304,
        repeat: 0x11121314,
        metaState: 0x21222324,
      ),
      <int>[0, 1, 1, 2, 3, 4, 0x11, 0x12, 0x13, 0x14, 0x21, 0x22, 0x23, 0x24],
    );
  });
}

List<int> _introduction(String name, String codec, int width, int height) {
  final bytes = Uint8List(77);
  bytes.setRange(1, 1 + name.length, name.codeUnits);
  bytes.setRange(65, 69, codec.codeUnits);
  final data = ByteData.sublistView(bytes);
  data.setUint32(69, width, Endian.big);
  data.setUint32(73, height, Endian.big);
  return bytes;
}

List<int> _packet(
  int pts, {
  bool isConfig = false,
  bool isKeyFrame = false,
  required List<int> data,
}) {
  final bytes = Uint8List(12 + data.length);
  final byteData = ByteData.sublistView(bytes);
  var ptsAndFlags = pts;
  if (isConfig) ptsAndFlags |= 1 << 63;
  if (isKeyFrame) ptsAndFlags |= 1 << 62;
  byteData.setUint64(0, ptsAndFlags, Endian.big);
  byteData.setUint32(8, data.length, Endian.big);
  bytes.setRange(12, bytes.length, data);
  return bytes;
}
