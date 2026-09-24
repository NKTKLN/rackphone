import 'dart:async';

import 'package:flutter/widgets.dart';

import 'decoder.dart';
import 'protocol.dart';
import 'screen_socket.dart';

enum ScreenState { connecting, live, heldByAnotherDevice, closed, failed }

/// The small transport surface the controller needs, kept fakeable without a
/// WebSocket or Flutter engine in controller and widget tests.
abstract interface class ScreenConnection {
  Stream<VideoPacket> get video;
  Stream<DeviceInfo> get device;
  Future<ScreenCloseReason> get closed;
  void send(List<int> controlMessage);
  Future<void> close();
}

/// Adapts the production socket to the fakeable controller boundary.
final class ScreenSocketConnection implements ScreenConnection {
  const ScreenSocketConnection(this.socket);

  final ScreenSocket socket;

  @override
  Future<ScreenCloseReason> get closed => socket.closed;
  @override
  Stream<DeviceInfo> get device => socket.device;
  @override
  Stream<VideoPacket> get video => socket.video;
  @override
  Future<void> close() => socket.close();
  @override
  void send(List<int> controlMessage) => socket.send(controlMessage);
}

typedef ScreenConnectionFactory = Future<ScreenConnection> Function();

/// Turns the screen transport and decoder into state directly renderable by a
/// page, while keeping transport failures out of framework callbacks.
final class ScreenController extends ChangeNotifier {
  ScreenController({required this.socketFactory, required this.decoder}) {
    _sizeSubscription = decoder.sizes.listen(_onSize);
  }

  StreamSubscription<({int width, int height})>? _sizeSubscription;

  /// The decoder saw the picture change size - the unit rotated. Touches are
  /// mapped through the device size, and scrcpy drops a touch whose frame of
  /// reference does not match its screen, so this has to follow at once.
  void _onSize(({int width, int height}) size) {
    final device = _device;
    if (device == null) return;
    if (device.width == size.width && device.height == size.height) return;
    _device = DeviceInfo(
      name: device.name,
      codec: device.codec,
      width: size.width,
      height: size.height,
    );
    if (!_disposed) notifyListeners();
  }

  final ScreenConnectionFactory socketFactory;
  final ScreenDecoder decoder;
  ScreenConnection? _connection;
  StreamSubscription<DeviceInfo>? _deviceSubscription;
  StreamSubscription<VideoPacket>? _videoSubscription;
  Future<void> _decoderWork = Future<void>.value();
  DeviceInfo? _device;
  ScreenState _state = ScreenState.connecting;
  String? _message;
  String? _holder;
  bool _disconnecting = false;
  bool _disposed = false;

  ScreenState get state => _state;
  String? get message => _message;

  /// The device holding this screen, set only while [state] says so.
  String? get holder => _holder;
  DeviceInfo? get device => _device;
  int? get textureId => decoder.textureId;

  Future<void> connect() async {
    if (_connection != null || _disconnecting || _disposed) return;
    _setState(ScreenState.connecting);
    try {
      final connection = await socketFactory();
      if (_disconnecting || _disposed) {
        await connection.close();
        return;
      }
      _connection = connection;
      _deviceSubscription = connection.device.listen(_onDevice, onError: _fail);
      _videoSubscription = connection.video.listen(_onPacket, onError: _fail);
      unawaited(connection.closed.then(_onClosed, onError: _fail));
    } catch (failure) {
      _fail(failure);
    }
  }

  void _onDevice(DeviceInfo device) {
    if (_device != null || _disconnecting) return;
    _device = device;
    _decoderWork = _runDecoderWork(() async {
      await decoder.configure(device);
      if (!_disconnecting) _setState(ScreenState.live);
    });
  }

  void _onPacket(VideoPacket packet) {
    _decoderWork = _runDecoderWork(() => decoder.feed(packet));
  }

  Future<void> _runDecoderWork(Future<void> Function() operation) async {
    try {
      await _decoderWork;
      await operation();
    } catch (failure, stackTrace) {
      _fail(failure, stackTrace);
    }
  }

  void _onClosed(ScreenCloseReason reason) {
    if (_disconnecting) return;
    _message = reason.message;
    _holder = reason.holder;
    _setState(switch (reason.kind) {
      ScreenCloseKind.closed => ScreenState.closed,
      ScreenCloseKind.heldByAnotherDevice => ScreenState.heldByAnotherDevice,
      ScreenCloseKind.networkFailure => ScreenState.failed,
    });
  }

  /// Maps through a contain-fit rectangle; subtracting its bars is what keeps
  /// taps aligned when the widget and device have different aspect ratios.
  void sendTouch(int action, Offset position, Size viewSize) {
    final connection = _connection;
    final device = _device;
    if (connection == null || device == null || _state != ScreenState.live) {
      return;
    }
    final scale = (viewSize.width / device.width).clamp(
      0.0,
      viewSize.height / device.height,
    );
    if (scale == 0) return;
    final displayedWidth = device.width * scale;
    final displayedHeight = device.height * scale;
    final left = (viewSize.width - displayedWidth) / 2;
    final top = (viewSize.height - displayedHeight) / 2;
    final x = ((position.dx - left) / scale).clamp(0, device.width - 1).round();
    final y = ((position.dy - top) / scale).clamp(0, device.height - 1).round();
    try {
      connection.send(
        encodeTouchEvent(
          action: action,
          pointerId: 0,
          x: x,
          y: y,
          screenWidth: device.width,
          screenHeight: device.height,
          pressure: action == 1 ? 0 : 1,
          buttons: 0,
        ),
      );
    } catch (failure) {
      _fail(failure);
    }
  }

  /// Presses and releases one Android key, such as back or home.
  void pressKey(int keycode) {
    for (final action in const <int>[0, 1]) {
      _send(
        encodeKeyEvent(
          action: action,
          keycode: keycode,
          repeat: 0,
          metaState: 0,
        ),
      );
    }
  }

  /// Turns the device a quarter, as scrcpy's own rotate shortcut does.
  void rotate() => _send(encodeRotateDevice());

  void _send(List<int> message) {
    final connection = _connection;
    if (connection == null || _state != ScreenState.live) return;
    try {
      connection.send(message);
    } catch (failure) {
      _fail(failure);
    }
  }

  Future<void> disconnect() async {
    if (_disconnecting) return;
    _disconnecting = true;
    try {
      await _deviceSubscription?.cancel();
      await _videoSubscription?.cancel();
      await _connection?.close();
    } catch (_) {
      // Closing is cleanup; a transport error here cannot improve screen state.
    }
    try {
      await _decoderWork;
    } catch (_) {
      // A decoder failure has already been translated into controller state.
    }
    try {
      await decoder.dispose();
    } catch (_) {
      // Disposal runs from widget teardown and must never escape that callback.
    }
    if (!_disposed) _setState(ScreenState.closed);
  }

  void _fail(Object failure, [StackTrace? _]) {
    if (_disconnecting || _disposed) return;
    _message = failure.toString();
    _setState(ScreenState.failed);
  }

  void _setState(ScreenState value) {
    _state = value;
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(_sizeSubscription?.cancel());
    unawaited(disconnect());
    super.dispose();
  }
}
