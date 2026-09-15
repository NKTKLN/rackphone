import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/gateway_client.dart';
import '../api/models.dart';
import 'audio.dart';
import 'call_socket.dart';
import 'protocol.dart';

enum CallState { idle, ringing, connecting, inCall, ended }

/// The transport surface needed by the call lifecycle, fakeable in tests.
abstract interface class CallAudioConnection {
  Future<CallAudioFormat> get ready;
  Stream<Uint8List> get downlink;
  Future<CallCloseReason> get closed;
  void sendUplink(Uint8List frame);
  Future<void> close();
}

final class CallSocketConnection implements CallAudioConnection {
  const CallSocketConnection(this.socket);

  final CallAudioSocket socket;

  @override
  Future<CallCloseReason> get closed => socket.closed;
  @override
  Stream<Uint8List> get downlink => socket.downlink;
  @override
  Future<CallAudioFormat> get ready => socket.ready;
  @override
  Future<void> close() => socket.close();
  @override
  void sendUplink(Uint8List frame) => socket.sendUplink(frame);
}

typedef CallConnectionFactory =
    Future<CallAudioConnection> Function(String unit);

/// Turns call events, controls, transport, and native audio into UI state.
final class CallController extends ChangeNotifier {
  CallController({
    required this.gateway,
    required Stream<GatewayEvent> events,
    required this.audio,
    CallConnectionFactory? socketFactory,
    GatewayEvent? initialEvent,
  }) : socketFactory =
           socketFactory ??
           ((unit) async =>
               CallSocketConnection(await gateway.callAudio(unit))) {
    _eventSubscription = events.listen(_onEvent, onError: _fail);
    if (initialEvent != null) _onEvent(initialEvent);
  }

  final GatewayCallsApi gateway;
  final CallAudio audio;
  final CallConnectionFactory socketFactory;
  late final StreamSubscription<GatewayEvent> _eventSubscription;
  StreamSubscription<Uint8List>? _downlinkSubscription;
  StreamSubscription<Uint8List>? _uplinkSubscription;
  CallAudioConnection? _connection;
  CallState _state = CallState.idle;
  String? _unit;
  String? _caller;
  String? _message;
  DateTime? _startedAt;
  bool _muted = false;
  bool _ending = false;
  bool _disposed = false;

  CallState get state => _state;
  String? get unit => _unit;
  String? get caller => _caller;
  String? get message => _message;
  DateTime? get startedAt => _startedAt;
  bool get muted => _muted;

  void _onEvent(GatewayEvent event) {
    if (event.kind != 'call') return;
    if (event.direction == 'ringing') {
      if (_state == CallState.connecting || _state == CallState.inCall) return;
      _unit = event.unit;
      _caller = event.address?.isEmpty == true ? null : event.address;
      _message = null;
      _muted = false;
      _startedAt = null;
      _setState(CallState.ringing);
      return;
    }
    if (_unit != event.unit || _state == CallState.idle) return;
    final address = event.address;
    if (address != null && address.isNotEmpty && _caller != address) return;
    unawaited(_end());
  }

  Future<void> accept() async {
    final unit = _unit;
    if (_state != CallState.ringing || unit == null) return;
    _setState(CallState.connecting);
    try {
      await gateway.answerCall(unit);
      if (_state != CallState.connecting || _ending || _disposed) return;
      final connection = await socketFactory(unit);
      if (_state != CallState.connecting || _ending || _disposed) {
        await connection.close();
        return;
      }
      _connection = connection;
      unawaited(connection.closed.then(_onClosed, onError: _fail));
      final format = await connection.ready;
      if (_state != CallState.connecting || _ending || _disposed) return;
      await audio.start(format);
      if (_state != CallState.connecting || _ending || _disposed) {
        await audio.dispose();
        return;
      }
      _downlinkSubscription = connection.downlink.listen(
        (frame) => unawaited(audio.play(frame)),
        onError: _fail,
      );
      _uplinkSubscription = audio.uplink.listen((frame) {
        if (!_muted && _state == CallState.inCall) {
          connection.sendUplink(frame);
        }
      }, onError: _fail);
      _startedAt = DateTime.now();
      _setState(CallState.inCall);
    } catch (failure, stackTrace) {
      _fail(failure, stackTrace);
      await _closeAudio();
    }
  }

  Future<void> reject() async {
    final unit = _unit;
    if (_state != CallState.ringing || unit == null) return;
    try {
      await gateway.rejectCall(unit);
      await _end();
    } catch (failure, stackTrace) {
      _fail(failure, stackTrace);
    }
  }

  Future<void> hangup() => _end();

  void toggleMute() {
    if (_state != CallState.inCall) return;
    _muted = !_muted;
    notifyListeners();
  }

  void dismissEnded() {
    if (_state != CallState.ended) return;
    _unit = null;
    _caller = null;
    _message = null;
    _setState(CallState.idle);
  }

  void _onClosed(CallCloseReason reason) {
    if (_ending || _disposed) return;
    _message = reason.kind == CallCloseKind.heldByAnotherDevice
        ? (reason.holder == null
              ? 'Call is held by another device.'
              : 'Call is held by ${reason.holder}.')
        : reason.message;
    unawaited(_end());
  }

  Future<void> _end() async {
    if (_ending || _state == CallState.idle || _state == CallState.ended) {
      return;
    }
    _ending = true;
    await _closeAudio();
    _startedAt = null;
    _muted = false;
    _ending = false;
    if (!_disposed) _setState(CallState.ended);
  }

  Future<void> _closeAudio() async {
    await _downlinkSubscription?.cancel();
    await _uplinkSubscription?.cancel();
    _downlinkSubscription = null;
    _uplinkSubscription = null;
    try {
      await _connection?.close();
    } catch (_) {
      // Closing is cleanup; the terminal call state is already known.
    }
    _connection = null;
    try {
      await audio.dispose();
    } catch (_) {
      // Native teardown must not escape a lifecycle callback.
    }
  }

  void _fail(Object failure, [StackTrace? _]) {
    if (_ending || _disposed) return;
    _message = failure.toString();
    unawaited(_end());
  }

  void _setState(CallState value) {
    _state = value;
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(_eventSubscription.cancel());
    unawaited(_closeAudio());
    super.dispose();
  }
}
