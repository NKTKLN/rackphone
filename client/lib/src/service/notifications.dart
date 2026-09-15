import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../api/models.dart';

/// Plugin-free text passed to the platform notification boundary.
final class ArrivalNotification {
  const ArrivalNotification({required this.title, required this.body});

  final String title;
  final String body;
}

/// Renders exactly the gateway-approved content into one arrivals channel.
final class ArrivalNotifications {
  ArrivalNotifications([FlutterLocalNotificationsPlugin? plugin])
    : _plugin = plugin ?? FlutterLocalNotificationsPlugin();

  static const _channelId = 'rackphone_arrivals';
  static const _channelName = 'Arrivals';
  static const _details = NotificationDetails(
    android: AndroidNotificationDetails(
      _channelId,
      _channelName,
      channelDescription: 'Messages, calls, and app notifications from units',
      importance: Importance.high,
      priority: Priority.high,
    ),
  );
  static const _incomingCallDetails = NotificationDetails(
    android: AndroidNotificationDetails(
      _channelId,
      _channelName,
      channelDescription: 'Messages, calls, and app notifications from units',
      importance: Importance.max,
      priority: Priority.max,
      category: AndroidNotificationCategory.call,
      fullScreenIntent: true,
    ),
  );

  final FlutterLocalNotificationsPlugin _plugin;

  /// Initializes without asking permission; the signed-in transition owns the
  /// prompt because it is the first moment the user has a reason to grant it.
  Future<void> initialize({void Function(GatewayEvent event)? onCall}) =>
      _plugin.initialize(
        const InitializationSettings(
          android: AndroidInitializationSettings('@mipmap/ic_launcher'),
          iOS: DarwinInitializationSettings(
            requestAlertPermission: false,
            requestBadgePermission: false,
            requestSoundPermission: false,
          ),
        ),
        onDidReceiveNotificationResponse: onCall == null
            ? null
            : (response) {
                final event = _callFromPayload(response.payload);
                if (event != null) onCall(event);
              },
      );

  Future<GatewayEvent?> callThatLaunchedApp() async {
    final launch = await _plugin.getNotificationAppLaunchDetails();
    if (launch?.didNotificationLaunchApp != true) return null;
    return _callFromPayload(launch?.notificationResponse?.payload);
  }

  Future<void> requestFullScreenPermission() async {
    await _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >()
        ?.requestFullScreenIntentPermission();
  }

  /// Builds notification text separately from [show] so policy tests never
  /// need to install a platform-channel mock.
  static ArrivalNotification contentFor(GatewayEvent event) {
    final sender = event.address?.isNotEmpty == true
        ? event.address!
        : 'Unknown';
    return switch (event.kind) {
      'sms' => ArrivalNotification(
        title: 'SMS from $sender',
        body: event.body ?? '',
      ),
      'call' => ArrivalNotification(title: 'Call from $sender', body: ''),
      'notification' => ArrivalNotification(
        title: 'Notification from $sender',
        body: event.body ?? '',
      ),
      _ => ArrivalNotification(title: event.kind, body: event.body ?? ''),
    };
  }

  /// Posts the event id as the Android notification id, preserving distinct
  /// arrivals while making a repeated SSE frame replace its earlier copy.
  Future<void> show(GatewayEvent event) {
    final content = contentFor(event);
    final details = event.kind == 'call' && event.direction == 'ringing'
        ? _incomingCallDetails
        : _details;
    return _plugin.show(
      event.id,
      content.title,
      content.body,
      details,
      payload: event.kind == 'call' ? _callPayload(event) : null,
    );
  }

  static String _callPayload(GatewayEvent event) =>
      jsonEncode(<String, Object?>{
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

  static GatewayEvent? _callFromPayload(String? payload) {
    if (payload == null || payload.isEmpty) return null;
    try {
      final json = jsonDecode(payload);
      if (json is! Map || json['kind'] != 'call') return null;
      return GatewayEvent.fromJson(
        json.map((key, value) => MapEntry(key.toString(), value)),
      );
    } on FormatException {
      return null;
    }
  }
}
