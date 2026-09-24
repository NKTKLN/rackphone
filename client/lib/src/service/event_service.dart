import 'dart:async';
import 'dart:ui';

import 'package:flutter_foreground_task/flutter_foreground_task.dart';

import '../api/errors.dart';
import '../api/gateway_client.dart';
import '../api/models.dart';
import '../session/token_store.dart';
import 'notifications.dart';
import 'service_settings.dart';

/// Owns the Android foreground-service boundary for the one delivery stream.
abstract final class EventService {
  static const _serviceId = 7419;
  static final StreamController<GatewayEvent> _callEvents =
      StreamController<GatewayEvent>.broadcast();
  static GatewayEvent? _latestCall;

  static Stream<GatewayEvent> get callEvents => _callEvents.stream;
  static GatewayEvent? get latestCall => _latestCall;

  /// Delivers notification launches through the same boundary as live SSE.
  static void publishCallEvent(GatewayEvent event) {
    if (event.kind != 'call') return;
    _latestCall = event.direction == 'ringing' ? event : null;
    _callEvents.add(event);
  }

  /// Configures the permanent notification without starting work on launch.
  static void initialize() {
    FlutterForegroundTask.initCommunicationPort();
    FlutterForegroundTask.addTaskDataCallback(_receiveTaskData);
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'rackphone_connection',
        channelName: 'Gateway connection',
        channelDescription: 'Keeps Rackphone connected for live arrivals',
        onlyAlertOnce: true,
      ),
      iosNotificationOptions: const IOSNotificationOptions(
        showNotification: false,
        playSound: false,
      ),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.nothing(),
        allowWakeLock: true,
      ),
    );
  }

  static void _receiveTaskData(Object data) {
    if (data is! Map) return;
    final json = data.map((key, value) => MapEntry(key.toString(), value));
    if (json['kind'] == 'call') {
      publishCallEvent(GatewayEvent.fromJson(json));
    }
  }

  /// Starts only after confirming that a durable session still exists.
  static Future<void> start(TokenStore tokenStore) async {
    final session = await tokenStore.read();
    if (session == null) return;
    final text = 'Listening for arrivals from ${session.baseUrl.host}';
    if (await FlutterForegroundTask.isRunningService) {
      await FlutterForegroundTask.updateService(
        notificationTitle: 'Rackphone connected',
        notificationText: text,
      );
      return;
    }
    await FlutterForegroundTask.startService(
      serviceId: _serviceId,
      notificationTitle: 'Rackphone connected',
      notificationText: text,
      callback: startEventService,
    );
  }

  /// Ends delivery immediately when the durable identity is removed.
  static Future<void> stop() async {
    if (await FlutterForegroundTask.isRunningService) {
      await FlutterForegroundTask.stopService();
    }
  }
}

/// Top-level entry point retained for the background Flutter engine.
@pragma('vm:entry-point')
void startEventService() {
  DartPluginRegistrant.ensureInitialized();
  FlutterForegroundTask.setTaskHandler(_EventTaskHandler());
}

final class _EventTaskHandler extends TaskHandler {
  GatewayClient? _client;
  bool _stopped = false;

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    unawaited(_listen());
  }

  Future<void> _listen() async {
    var retrySeconds = 2;
    while (!_stopped) {
      final store = SecureTokenStore();
      final session = await store.read();
      if (session == null) {
        await FlutterForegroundTask.stopService();
        return;
      }

      final client = GatewayClient(
        baseUrl: session.baseUrl,
        refreshTokenProvider: () async => (await store.read())?.refreshToken,
        onTokensRenewed: (tokens) => _persistTokens(store, session, tokens),
      );
      _client = client;
      try {
        final notifications = ArrivalNotifications();
        await notifications.initialize();
        await for (final event in client.stream()) {
          retrySeconds = 2;
          if (event.kind == 'call') {
            FlutterForegroundTask.sendDataToMain(<String, Object?>{
              'id': event.id,
              'unit': event.unit,
              'kind': event.kind,
              'address': event.address,
              'body': event.body,
              'ts': event.timestamp,
              'direction': event.direction,
              'duration': event.duration,
              'received_at': event.receivedAt,
            });
          }
          final settings = await ServiceSettings.read();
          // A message this gateway sent is news to nobody.
          if (event.direction != 'out' &&
              settings.shouldNotify(event.kind, DateTime.now())) {
            await notifications.show(event);
          }
        }
      } on GatewayAuthException {
        await store.clear();
        await FlutterForegroundTask.stopService();
        return;
      } catch (_) {
        // Every non-authentication failure is a dropped connection and follows
        // the same bounded retry below.
      } finally {
        client.close();
        if (identical(_client, client)) _client = null;
      }

      if (_stopped) return;
      // Never retry faster: a tight loop on a phone is both a battery fire and
      // a request flood. Delays grow 2, 4, 8 ... and cap at 60 seconds.
      await Future<void>.delayed(Duration(seconds: retrySeconds));
      retrySeconds = (retrySeconds * 2).clamp(2, 60);
    }
  }

  Future<void> _persistTokens(
    TokenStore store,
    StoredSession original,
    Tokens tokens,
  ) async {
    final current = await store.read() ?? original;
    await store.write(
      StoredSession(
        baseUrl: current.baseUrl,
        refreshToken: tokens.refreshToken,
        deviceLabel: current.deviceLabel,
        scope: tokens.scope,
        refreshExpiresAt: tokens.refreshExpiresAt,
      ),
    );
  }

  @override
  void onRepeatEvent(DateTime timestamp) {}

  @override
  Future<void> onDestroy(DateTime timestamp) async {
    _stopped = true;
    _client?.close();
  }
}
