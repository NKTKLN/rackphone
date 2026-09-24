import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'protocol.dart';

enum CallCloseKind { closed, heldByAnotherDevice, networkFailure }

const callSessionBusyReason = 'session_busy';

final class CallCloseReason {
  const CallCloseReason(this.kind, this.message);

  final CallCloseKind kind;
  final String message;

  String? get holder {
    if (kind != CallCloseKind.heldByAnotherDevice) return null;
    final name = message.replaceFirst(callSessionBusyReason, '').trim();
    return name.isEmpty ? null : name;
  }
}

final class CallSocketConnectionException implements Exception {
  const CallSocketConnectionException(this.cause);

  final Object cause;

  @override
  String toString() => 'Could not connect to call audio: $cause';
}

/// Carries the raw, bidirectional PCM stream through the gateway.
final class CallAudioSocket {
  CallAudioSocket._(this._socket) {
    // A caller that only watches `closed` - a session refused as busy closes
    // before any header - still leaves `ready` to error. Swallow it here so it
    // is never an unhandled async error; a real awaiter of `ready` still gets
    // the error through its own subscription.
    unawaited(_ready.future.then((_) {}, onError: (Object _, StackTrace _) {}));
    _socket.listen(
      _route,
      onError: _onError,
      onDone: _onDone,
      cancelOnError: false,
    );
    _formatSubscription = _parser.format.listen((format) {
      sampleRate = format.sampleRate;
      frameBytes = format.frameBytes;
      if (!_ready.isCompleted) _ready.complete(format);
    }, onError: _onParserError);
  }

  static Future<CallAudioSocket> connect({
    required Uri uri,
    required String accessToken,
  }) async {
    try {
      final socket = await WebSocket.connect(
        uri.toString(),
        headers: <String, String>{'Authorization': 'Bearer $accessToken'},
      );
      return CallAudioSocket._(socket);
    } catch (error) {
      throw CallSocketConnectionException(error);
    }
  }

  final WebSocket _socket;
  final CallAudioStreamParser _parser = CallAudioStreamParser();
  final Completer<CallAudioFormat> _ready = Completer<CallAudioFormat>();
  final Completer<CallCloseReason> _closed = Completer<CallCloseReason>();
  late final StreamSubscription<CallAudioFormat> _formatSubscription;
  bool _closeRequested = false;

  int? sampleRate;
  int? frameBytes;
  Future<CallAudioFormat> get ready => _ready.future;
  Stream<Uint8List> get downlink => _parser.frames;
  Future<CallCloseReason> get closed => _closed.future;

  void sendUplink(Uint8List frame) {
    final expected = frameBytes;
    if (expected == null || frame.length != expected) return;
    _socket.add(frame);
  }

  Future<void> close() async {
    if (_closeRequested) return;
    _closeRequested = true;
    await _socket.close();
  }

  void _route(dynamic message) {
    if (message is List<int>) _parser.add(message);
  }

  void _onParserError(Object error, StackTrace stackTrace) {
    if (!_ready.isCompleted) _ready.completeError(error, stackTrace);
    _onError(error, stackTrace);
  }

  void _onError(Object error, StackTrace stackTrace) {
    if (!_ready.isCompleted) _ready.completeError(error, stackTrace);
    if (!_closed.isCompleted) {
      _closed.complete(
        CallCloseReason(CallCloseKind.networkFailure, error.toString()),
      );
    }
  }

  void _onDone() {
    final code = _socket.closeCode;
    final reason = _socket.closeReason ?? '';
    if (!_ready.isCompleted) {
      _ready.completeError(
        CallSocketConnectionException(
          reason.isEmpty ? 'Connection closed before the audio header' : reason,
        ),
      );
    }
    if (!_closed.isCompleted) {
      if (code == WebSocketStatus.policyViolation &&
          reason.startsWith(callSessionBusyReason)) {
        _closed.complete(
          CallCloseReason(CallCloseKind.heldByAnotherDevice, reason),
        );
      } else if (_closeRequested || code == WebSocketStatus.normalClosure) {
        _closed.complete(CallCloseReason(CallCloseKind.closed, reason));
      } else {
        _closed.complete(
          CallCloseReason(
            CallCloseKind.networkFailure,
            reason.isEmpty ? 'Call audio connection was lost.' : reason,
          ),
        );
      }
    }
    unawaited(_finishStreams());
  }

  Future<void> _finishStreams() async {
    await _formatSubscription.cancel();
    await _parser.close();
  }
}
