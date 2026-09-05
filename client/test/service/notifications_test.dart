import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/service/notifications.dart';

void main() {
  group('ArrivalNotifications.contentFor', () {
    test('shows an SMS sender and carries its text verbatim', () {
      final content = ArrivalNotifications.contentFor(
        _event(kind: 'sms', address: '+15551234', body: '  hello\nthere  '),
      );

      expect(content.title, 'SMS from +15551234');
      expect(content.body, '  hello\nthere  ');
    });

    test('shows who called', () {
      final content = ArrivalNotifications.contentFor(
        _event(kind: 'call', address: '+15559876'),
      );

      expect(content.title, 'Call from +15559876');
      expect(content.body, isEmpty);
    });

    test('shows the source app and carries its text verbatim', () {
      final content = ArrivalNotifications.contentFor(
        _event(
          kind: 'notification',
          address: 'org.example.chat',
          body: 'New message: <unchanged>',
        ),
      );

      expect(content.title, 'Notification from org.example.chat');
      expect(content.body, 'New message: <unchanged>');
    });
  });
}

GatewayEvent _event({
  required String kind,
  required String address,
  String? body,
}) => GatewayEvent(
  id: 17,
  unit: 'lisa01',
  kind: kind,
  address: address,
  body: body,
  timestamp: null,
  direction: null,
  duration: null,
  receivedAt: null,
);
