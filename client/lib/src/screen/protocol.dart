import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

/// The scrcpy server version whose framing is implemented in this file.
///
/// Keep the version beside the parser: a server bump has exactly one place to
/// audit for wire-format changes.
const scrcpyProtocolVersion = '3.3.1';

/// The identity and initial dimensions announced by the video stream.
final class DeviceInfo {
  const DeviceInfo({
    required this.name,
    required this.codec,
    required this.width,
    required this.height,
  });

  final String name;
  final String codec;
  final int width;
  final int height;
}

/// One encoded H.264 packet, still untouched by a decoder.
final class VideoPacket {
  VideoPacket({
    required this.pts,
    required this.isConfig,
    required this.isKeyFrame,
    required List<int> data,
  }) : data = List<int>.unmodifiable(data);

  final int pts;
  final bool isConfig;
  final bool isKeyFrame;
  final List<int> data;
}

/// Incrementally parses the scrcpy [scrcpyProtocolVersion] video stream.
///
/// TCP and relay chunks are merely the bytes available at that instant. A
/// parser which treats them as protocol boundaries is the classic source of a
/// stream that works on a desk and fails once it crosses a real rack network.
final class VideoStreamParser {
  final StreamController<DeviceInfo> _device = StreamController<DeviceInfo>();
  final StreamController<VideoPacket> _video = StreamController<VideoPacket>();
  final List<int> _buffer = <int>[];
  bool _hasDeviceInfo = false;
  int? _packetLength;
  int _packetPts = 0;
  bool _packetIsConfig = false;
  bool _packetIsKeyFrame = false;

  Stream<DeviceInfo> get device => _device.stream;
  Stream<VideoPacket> get video => _video.stream;

  /// Adds an arbitrary fragment of the video TCP stream.
  void add(List<int> chunk) {
    if (chunk.isEmpty) return;
    _buffer.addAll(chunk);
    _parse();
  }

  /// Closes the output streams after the transport has ended.
  Future<void> close() async {
    await _device.close();
    await _video.close();
  }

  void _parse() {
    while (true) {
      if (!_hasDeviceInfo) {
        const introductionLength = 1 + 64 + 4 + 4 + 4;
        if (_buffer.length < introductionLength) return;
        final introduction = _take(introductionLength);
        final nameBytes = introduction.sublist(1, 65);
        final zero = nameBytes.indexOf(0);
        final codecBytes = introduction.sublist(65, 69);
        _device.add(
          DeviceInfo(
            name: utf8.decode(
              zero < 0 ? nameBytes : nameBytes.sublist(0, zero),
            ),
            codec: ascii.decode(codecBytes.where((byte) => byte != 0).toList()),
            width: _uint32(introduction, 69),
            height: _uint32(introduction, 73),
          ),
        );
        _hasDeviceInfo = true;
      }

      if (_packetLength == null) {
        if (_buffer.length < 12) return;
        final header = _take(12);
        final ptsAndFlags = _uint64(header, 0);
        _packetIsConfig = ptsAndFlags & (1 << 63) != 0;
        _packetIsKeyFrame = ptsAndFlags & (1 << 62) != 0;
        _packetPts = ptsAndFlags & ((1 << 62) - 1);
        _packetLength = _uint32(header, 8);
      }

      final length = _packetLength!;
      if (_buffer.length < length) return;
      _video.add(
        VideoPacket(
          pts: _packetPts,
          isConfig: _packetIsConfig,
          isKeyFrame: _packetIsKeyFrame,
          data: _take(length),
        ),
      );
      _packetLength = null;
    }
  }

  List<int> _take(int count) {
    final result = _buffer.sublist(0, count);
    _buffer.removeRange(0, count);
    return result;
  }
}

/// Encodes a scrcpy touch event using device-pixel coordinates.
///
/// The screen size accompanies every point because the device can rotate
/// between two events; without that frame of reference the same point lands
/// somewhere else. scrcpy's action-button field is zero for touchscreen input.
List<int> encodeTouchEvent({
  required int action,
  required int pointerId,
  required int x,
  required int y,
  required int screenWidth,
  required int screenHeight,
  required double pressure,
  required int buttons,
}) {
  if (pressure < 0 || pressure > 1) {
    throw RangeError.range(pressure, 0, 1, 'pressure');
  }
  final bytes = Uint8List(32);
  final data = ByteData.sublistView(bytes);
  bytes[0] = 2;
  bytes[1] = action;
  data.setUint64(2, pointerId, Endian.big);
  data.setUint32(10, x, Endian.big);
  data.setUint32(14, y, Endian.big);
  data.setUint16(18, screenWidth, Endian.big);
  data.setUint16(20, screenHeight, Endian.big);
  data.setUint16(
    22,
    pressure == 1 ? 0xffff : (pressure * 0x10000).truncate(),
    Endian.big,
  );
  data.setUint32(24, 0, Endian.big);
  data.setUint32(28, buttons, Endian.big);
  return bytes;
}

/// Encodes a scrcpy Android key event.
List<int> encodeKeyEvent({
  required int action,
  required int keycode,
  required int repeat,
  required int metaState,
}) {
  final bytes = Uint8List(14);
  final data = ByteData.sublistView(bytes);
  bytes[0] = 0;
  bytes[1] = action;
  data.setUint32(2, keycode, Endian.big);
  data.setUint32(6, repeat, Endian.big);
  data.setUint32(10, metaState, Endian.big);
  return bytes;
}

int _uint32(List<int> bytes, int offset) => ByteData.sublistView(
  Uint8List.fromList(bytes),
  offset,
  offset + 4,
).getUint32(0, Endian.big);

int _uint64(List<int> bytes, int offset) => ByteData.sublistView(
  Uint8List.fromList(bytes),
  offset,
  offset + 8,
).getUint64(0, Endian.big);
