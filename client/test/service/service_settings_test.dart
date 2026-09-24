import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/service/service_settings.dart';

void main() {
  group('ServiceSettings.shouldNotify', () {
    test('uses the notification defaults for every event kind', () {
      const settings = ServiceSettings();
      final noon = DateTime(2026, 1, 2, 12);

      expect(settings.shouldNotify('sms', noon), isTrue);
      expect(settings.shouldNotify('call', noon), isTrue);
      expect(settings.shouldNotify('notification', noon), isFalse);
    });

    test('honors each event-kind switch', () {
      const settings = ServiceSettings(
        notifyOnSms: false,
        notifyOnCalls: false,
        notifyOnNotifications: true,
      );
      final noon = DateTime(2026, 1, 2, 12);

      expect(settings.shouldNotify('sms', noon), isFalse);
      expect(settings.shouldNotify('call', noon), isFalse);
      expect(settings.shouldNotify('notification', noon), isTrue);
    });

    test('suppresses events inside ordinary quiet hours', () {
      const settings = ServiceSettings(
        quietStartMinute: 8 * 60,
        quietEndMinute: 10 * 60,
      );

      expect(settings.shouldNotify('sms', DateTime(2026, 1, 2, 9)), isFalse);
      expect(settings.shouldNotify('sms', DateTime(2026, 1, 2, 10)), isTrue);
      expect(settings.shouldNotify('sms', DateTime(2026, 1, 2, 12)), isTrue);
    });

    test('suppresses both sides of quiet hours that wrap midnight', () {
      const settings = ServiceSettings(
        quietStartMinute: 23 * 60,
        quietEndMinute: 8 * 60,
      );

      expect(
        settings.shouldNotify('call', DateTime(2026, 1, 2, 23, 30)),
        isFalse,
      );
      expect(
        settings.shouldNotify('call', DateTime(2026, 1, 3, 7, 59)),
        isFalse,
      );
      expect(settings.shouldNotify('call', DateTime(2026, 1, 3, 8)), isTrue);
      expect(settings.shouldNotify('call', DateTime(2026, 1, 2, 12)), isTrue);
    });
  });

  test('an unknown kind is shown rather than silently dropped', () {
    // The gateway stops pushing to ntfy while this client is connected, so a
    // kind dropped here is lost on both channels.
    const settings = ServiceSettings();
    expect(settings.shouldNotify('mms', DateTime(2026, 9, 5, 12)), isTrue);
  });
}
