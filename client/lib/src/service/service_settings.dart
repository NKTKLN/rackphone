import 'package:flutter_foreground_task/flutter_foreground_task.dart';

/// Operator-owned notification policy shared with the service isolate.
final class ServiceSettings {
  const ServiceSettings({
    this.notifyOnSms = true,
    this.notifyOnCalls = true,
    this.notifyOnNotifications = false,
    this.quietStartMinute,
    this.quietEndMinute,
  }) : assert(
         (quietStartMinute == null) == (quietEndMinute == null),
         'Quiet hours need both a start and an end.',
       ),
       assert(
         quietStartMinute == null ||
             (quietStartMinute >= 0 && quietStartMinute < 24 * 60),
         'The quiet-hours start must be a minute of day.',
       ),
       assert(
         quietEndMinute == null ||
             (quietEndMinute >= 0 && quietEndMinute < 24 * 60),
         'The quiet-hours end must be a minute of day.',
       );

  static const _notifyOnSmsKey = 'service.notify_on_sms';
  static const _notifyOnCallsKey = 'service.notify_on_calls';
  static const _notifyOnNotificationsKey = 'service.notify_on_notifications';
  static const _quietStartMinuteKey = 'service.quiet_start_minute';
  static const _quietEndMinuteKey = 'service.quiet_end_minute';

  final bool notifyOnSms;
  final bool notifyOnCalls;
  final bool notifyOnNotifications;
  final int? quietStartMinute;
  final int? quietEndMinute;

  /// Reads through the foreground-task store because it reloads shared
  /// preferences before every read, making writes visible across isolates.
  static Future<ServiceSettings> read() async {
    final values = await FlutterForegroundTask.getAllData();
    final start = values[_quietStartMinuteKey];
    final end = values[_quietEndMinuteKey];
    final validQuietHours =
        start is int &&
        end is int &&
        start >= 0 &&
        start < 24 * 60 &&
        end >= 0 &&
        end < 24 * 60;
    return ServiceSettings(
      notifyOnSms: values[_notifyOnSmsKey] as bool? ?? true,
      notifyOnCalls: values[_notifyOnCallsKey] as bool? ?? true,
      notifyOnNotifications:
          values[_notifyOnNotificationsKey] as bool? ?? false,
      quietStartMinute: validQuietHours ? start : null,
      quietEndMinute: validQuietHours ? end : null,
    );
  }

  /// Persists one complete policy so the app and service cannot disagree over
  /// defaults after either isolate is recreated.
  Future<void> save() async {
    await FlutterForegroundTask.saveData(
      key: _notifyOnSmsKey,
      value: notifyOnSms,
    );
    await FlutterForegroundTask.saveData(
      key: _notifyOnCallsKey,
      value: notifyOnCalls,
    );
    await FlutterForegroundTask.saveData(
      key: _notifyOnNotificationsKey,
      value: notifyOnNotifications,
    );
    final start = quietStartMinute;
    final end = quietEndMinute;
    if (start == null || end == null) {
      await FlutterForegroundTask.removeData(key: _quietStartMinuteKey);
      await FlutterForegroundTask.removeData(key: _quietEndMinuteKey);
    } else {
      await FlutterForegroundTask.saveData(
        key: _quietStartMinuteKey,
        value: start,
      );
      await FlutterForegroundTask.saveData(key: _quietEndMinuteKey, value: end);
    }
  }

  /// Decides locally so filtering never needs a platform channel or network.
  bool shouldNotify(String kind, DateTime at) {
    final enabled = switch (kind) {
      'sms' => notifyOnSms,
      'call' => notifyOnCalls,
      'notification' => notifyOnNotifications,
      // A kind this build has never heard of is shown rather than dropped.
      // While this client holds its stream the gateway suppresses its own ntfy
      // push, so anything discarded here is lost on both channels at once - and
      // an unconfigured kind is far more likely to be a message than noise.
      _ => true,
    };
    if (!enabled) return false;

    final start = quietStartMinute;
    final end = quietEndMinute;
    if (start == null || end == null || start == end) return true;
    final minute = at.hour * 60 + at.minute;
    final isQuiet = start < end
        ? minute >= start && minute < end
        : minute >= start || minute < end;
    return !isQuiet;
  }
}
