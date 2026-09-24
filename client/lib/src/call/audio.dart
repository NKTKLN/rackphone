import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/services.dart';

import 'protocol.dart';

/// Native duplex audio kept behind a boundary that controller tests can fake.
abstract interface class CallAudio {
  Stream<Uint8List> get uplink;

  Future<void> start(CallAudioFormat format);
  Future<void> play(Uint8List frame);
  Future<void> dispose();
}

/// AudioTrack and AudioRecord bridge hosted by the Android activity.
final class HardwareCallAudio implements CallAudio {
  HardwareCallAudio([BasicMessageChannel<ByteData?>? channel])
    : _channel =
          channel ??
          const BasicMessageChannel<ByteData?>(
            'com.nktkln.rackphone.client/call_audio',
            BinaryCodec(),
          );

  final BasicMessageChannel<ByteData?> _channel;

  // The native side's reply byte: see CallAudio.kt.
  static const int _deniedReply = 0;
  static const int _startedReply = 1;
  static const int _unavailableReply = 2;
  final StreamController<Uint8List> _uplink =
      StreamController<Uint8List>.broadcast();
  bool _started = false;

  @override
  Stream<Uint8List> get uplink => _uplink.stream;

  @override
  Future<void> start(CallAudioFormat format) async {
    if (_started) return;
    _channel.setMessageHandler((message) async {
      if (message != null && message.lengthInBytes != 0) {
        _uplink.add(
          Uint8List.fromList(
            message.buffer.asUint8List(
              message.offsetInBytes,
              message.lengthInBytes,
            ),
          ),
        );
      }
      return null;
    });
    final message = ByteData(9)
      ..setUint8(0, 0)
      ..setUint32(1, format.sampleRate, Endian.big)
      ..setUint32(5, format.frameBytes, Endian.big);
    final reply = await _channel.send(message);
    final outcome = reply == null || reply.lengthInBytes == 0
        ? _unavailableReply
        : reply.getUint8(0);
    if (outcome != _startedReply) {
      _channel.setMessageHandler(null);
      throw StateError(
        outcome == _deniedReply
            ? 'Microphone permission was denied'
            : 'The microphone could not be opened. Another call or app may '
                  'be using it.',
      );
    }
    _started = true;
  }

  @override
  Future<void> play(Uint8List frame) async {
    if (!_started) return;
    final message = ByteData(1 + frame.length)..setUint8(0, 1);
    message.buffer.asUint8List(1).setAll(0, frame);
    await _channel.send(message);
  }

  @override
  Future<void> dispose() async {
    if (_started) await _channel.send(ByteData(1)..setUint8(0, 2));
    _started = false;
    _channel.setMessageHandler(null);
  }
}

/// In-memory audio bridge for controller tests without an Android engine.
final class FakeCallAudio implements CallAudio {
  final StreamController<Uint8List> captured = StreamController<Uint8List>();
  final List<Uint8List> played = <Uint8List>[];
  CallAudioFormat? format;
  int disposeCalls = 0;

  @override
  Stream<Uint8List> get uplink => captured.stream;

  @override
  Future<void> start(CallAudioFormat format) async => this.format = format;

  @override
  Future<void> play(Uint8List frame) async {
    played.add(Uint8List.fromList(frame));
  }

  @override
  Future<void> dispose() async {
    disposeCalls++;
    format = null;
  }
}
