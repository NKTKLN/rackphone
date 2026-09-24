import 'dart:async';
import 'dart:typed_data';

/// The audio format announced once at the start of a call stream.
final class CallAudioFormat {
  const CallAudioFormat({required this.sampleRate, required this.frameBytes});

  final int sampleRate;
  final int frameBytes;
}

/// Incrementally separates the call header and fixed-size PCM frames.
///
/// Relay chunks are only the bytes currently available. Neither the eight-byte
/// introduction nor an audio frame is required to share their boundaries.
final class CallAudioStreamParser {
  final StreamController<CallAudioFormat> _format =
      StreamController<CallAudioFormat>();
  final StreamController<Uint8List> _frames = StreamController<Uint8List>();
  final List<int> _buffer = <int>[];
  CallAudioFormat? _announced;

  Stream<CallAudioFormat> get format => _format.stream;
  Stream<Uint8List> get frames => _frames.stream;

  void add(List<int> chunk) {
    if (chunk.isEmpty) return;
    _buffer.addAll(chunk);
    _parse();
  }

  Future<void> close() async {
    await _format.close();
    await _frames.close();
  }

  void _parse() {
    if (_announced == null) {
      if (_buffer.length < 8) return;
      final header = Uint8List.fromList(_take(8));
      final data = ByteData.sublistView(header);
      final announced = CallAudioFormat(
        sampleRate: data.getUint32(0, Endian.big),
        frameBytes: data.getUint32(4, Endian.big),
      );
      if (announced.sampleRate <= 0 || announced.frameBytes <= 0) {
        _format.addError(const FormatException('Invalid call audio header'));
        return;
      }
      _announced = announced;
      _format.add(announced);
    }

    final frameBytes = _announced!.frameBytes;
    while (_buffer.length >= frameBytes) {
      _frames.add(Uint8List.fromList(_take(frameBytes)));
    }
  }

  List<int> _take(int count) {
    final result = _buffer.sublist(0, count);
    _buffer.removeRange(0, count);
    return result;
  }
}
