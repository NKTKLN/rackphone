import 'dart:async';
import 'dart:io';

import 'protocol.dart';

/// The category of shutdown a screen should present to its operator.
enum ScreenCloseKind { closed, heldByAnotherDevice, networkFailure }

/// A transport shutdown translated into language suitable for screen UI.
/// The token the gateway puts first in its policy-close reason.
///
/// Matched instead of the sentence around it: telling "someone else holds this
/// screen" from "the network died" is the difference between waiting and
/// reconnecting, and a reworded message on the other side of the wire must not
/// be able to swap one for the other in silence.
const sessionBusyReason = 'session_busy';

final class ScreenCloseReason {
  const ScreenCloseReason(this.kind, this.message);

  final ScreenCloseKind kind;
  final String message;
}

/// A failure which prevented the screen WebSocket from being established.
final class ScreenSocketConnectionException implements Exception {
  const ScreenSocketConnectionException(this.cause);

  final Object cause;

  @override
  String toString() => 'Could not connect to the screen: $cause';
}

/// Carries channel-framed video and control traffic through the gateway.
final class ScreenSocket {
  ScreenSocket._(this._socket) {
    _socket.listen(
      _route,
      onError: _onError,
      onDone: _onDone,
      cancelOnError: false,
    );
  }

  /// Opens an authenticated screen WebSocket.
  static Future<ScreenSocket> connect({
    required Uri uri,
    required String accessToken,
  }) async {
    try {
      final socket = await WebSocket.connect(
        uri.toString(),
        headers: <String, String>{'Authorization': 'Bearer $accessToken'},
      );
      return ScreenSocket._(socket);
    } catch (error) {
      throw ScreenSocketConnectionException(error);
    }
  }

  final WebSocket _socket;
  final VideoStreamParser _parser = VideoStreamParser();
  final StreamController<List<int>> _control = StreamController<List<int>>();
  final Completer<ScreenCloseReason> _closed = Completer<ScreenCloseReason>();
  bool _closeRequested = false;

  Stream<VideoPacket> get video => _parser.video;
  Stream<DeviceInfo> get device => _parser.device;
  Stream<List<int>> get control => _control.stream;
  Future<ScreenCloseReason> get closed => _closed.future;

  /// Sends one scrcpy control message on relay channel 1.
  void send(List<int> controlMessage) {
    _socket.add(<int>[1, ...controlMessage]);
  }

  /// Closes the underlying socket; repeated calls have no additional effect.
  Future<void> close() async {
    if (_closeRequested) return;
    _closeRequested = true;
    await _socket.close();
  }

  void _route(dynamic message) {
    if (message is! List<int> || message.isEmpty) return;
    switch (message.first) {
      case 0:
        _parser.add(message.sublist(1));
      case 1:
        _control.add(List<int>.unmodifiable(message.sublist(1)));
    }
  }

  void _onError(Object error, StackTrace stackTrace) {
    if (!_closed.isCompleted) {
      _closed.complete(
        ScreenCloseReason(ScreenCloseKind.networkFailure, error.toString()),
      );
    }
  }

  void _onDone() {
    if (!_closed.isCompleted) {
      final code = _socket.closeCode;
      final reason = _socket.closeReason ?? '';
      if (code == WebSocketStatus.policyViolation &&
          reason.startsWith(sessionBusyReason)) {
        _closed.complete(
          ScreenCloseReason(ScreenCloseKind.heldByAnotherDevice, reason),
        );
      } else if (_closeRequested || code == WebSocketStatus.normalClosure) {
        _closed.complete(ScreenCloseReason(ScreenCloseKind.closed, reason));
      } else {
        _closed.complete(
          ScreenCloseReason(
            ScreenCloseKind.networkFailure,
            reason.isEmpty ? 'Screen connection was lost.' : reason,
          ),
        );
      }
    }
    unawaited(_finishStreams());
  }

  Future<void> _finishStreams() async {
    await _parser.close();
    await _control.close();
  }
}
