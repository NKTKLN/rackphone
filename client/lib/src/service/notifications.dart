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

  final FlutterLocalNotificationsPlugin _plugin;

  /// Initializes without asking permission; the signed-in transition owns the
  /// prompt because it is the first moment the user has a reason to grant it.
  Future<void> initialize() => _plugin.initialize(
    const InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      iOS: DarwinInitializationSettings(
        requestAlertPermission: false,
        requestBadgePermission: false,
        requestSoundPermission: false,
      ),
    ),
  );

  /// Builds notification text separately from [show] so policy tests never
  /// need to install a platform-channel mock.
  static ArrivalNotification contentFor(GatewayEvent event) {
    final sender = event.address ?? 'Unknown';
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
    return _plugin.show(event.id, content.title, content.body, _details);
  }
}
