import 'dart:typed_data';

import 'package:flutter/services.dart';

import 'protocol.dart';

/// A screen decoder boundary that widgets can hold without owning an engine.
///
/// The fake implementation keeps screen tests independent of Android's codec,
/// surface, and texture registry, for the same reason companion controls sit
/// behind an interface instead of being bare platform-channel calls.
abstract interface class ScreenDecoder {
  int? get textureId;

  Future<void> configure(DeviceInfo device);

  Future<void> feed(VideoPacket packet);

  Future<void> dispose();
}

/// Hardware-backed H.264 decoding through the client's Android activity.
final class HardwareScreenDecoder implements ScreenDecoder {
  HardwareScreenDecoder([BasicMessageChannel<ByteData?>? channel])
    : _channel =
          channel ??
          const BasicMessageChannel<ByteData?>(
            'com.nktkln.rackphone.client/screen',
            BinaryCodec(),
          );

  final BasicMessageChannel<ByteData?> _channel;
  DeviceInfo? _device;

  @override
  int? textureId;

  @override
  Future<void> configure(DeviceInfo device) async {
    final current = _device;
    if (current == null) {
      final reply = await _channel.send(_dimensionsMessage(0, device));
      if (reply == null || reply.lengthInBytes < 8) {
        throw StateError('Android did not return a screen texture id');
      }
      textureId = reply.getInt64(0, Endian.big);
    } else if (_dimensionsChanged(current, device)) {
      // scrcpy announces rotation dimensions before its fresh config packet.
      // Rebuild now: decoding that packet with the old size produces garbage
      // rather than an error that could be used as a recovery signal.
      await _channel.send(_dimensionsMessage(2, device));
    }
    _device = device;
  }

  @override
  Future<void> feed(VideoPacket packet) async {
    // Never retain encoded frames before a codec exists. Playing that stale
    // backlog later is seconds of invisible operator lag, worse than a gap.
    if (_device == null || textureId == null) return;
    final message = ByteData(2 + packet.data.length);
    message.setUint8(0, 1);
    message.setUint8(1, packet.isConfig ? 1 : 0);
    message.buffer.asUint8List(2).setAll(0, packet.data);
    await _channel.send(message);
  }

  @override
  Future<void> dispose() async {
    if (_device == null && textureId == null) return;
    await _channel.send(ByteData(1)..setUint8(0, 3));
    _device = null;
    textureId = null;
  }

  ByteData _dimensionsMessage(int operation, DeviceInfo device) => ByteData(9)
    ..setUint8(0, operation)
    ..setUint32(1, device.width, Endian.big)
    ..setUint32(5, device.height, Endian.big);
}

/// One observable fake-decoder operation, retained in call order for tests.
sealed class ScreenDecoderCall {
  const ScreenDecoderCall();
}

final class ConfigureDecoderCall extends ScreenDecoderCall {
  const ConfigureDecoderCall(this.device, {required this.reconfigure});

  final DeviceInfo device;
  final bool reconfigure;
}

final class FeedDecoderCall extends ScreenDecoderCall {
  const FeedDecoderCall(this.packet);

  final VideoPacket packet;
}

final class DisposeDecoderCall extends ScreenDecoderCall {
  const DisposeDecoderCall();
}

/// In-memory decoder for widget and controller tests with no Android engine.
final class FakeScreenDecoder implements ScreenDecoder {
  FakeScreenDecoder({this.configuredTextureId = 1});

  final int configuredTextureId;
  final List<ScreenDecoderCall> calls = <ScreenDecoderCall>[];
  DeviceInfo? _device;

  @override
  int? textureId;

  @override
  Future<void> configure(DeviceInfo device) async {
    final reconfigure = _device != null && _dimensionsChanged(_device!, device);
    calls.add(ConfigureDecoderCall(device, reconfigure: reconfigure));
    _device = device;
    textureId = configuredTextureId;
  }

  @override
  Future<void> feed(VideoPacket packet) async {
    if (_device == null || textureId == null) return;
    calls.add(FeedDecoderCall(packet));
  }

  @override
  Future<void> dispose() async {
    calls.add(const DisposeDecoderCall());
    _device = null;
    textureId = null;
  }
}

bool _dimensionsChanged(DeviceInfo current, DeviceInfo announced) =>
    current.width != announced.width || current.height != announced.height;
