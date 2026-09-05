import 'package:flutter/material.dart';

import '../api/models.dart';

/// A compact, scannable event row shared by the full feed and SMS view.
class EventTile extends StatelessWidget {
  const EventTile({required this.event, super.key});

  final GatewayEvent event;

  @override
  Widget build(BuildContext context) {
    final address = event.address;
    final body = event.body;
    return ListTile(
      leading: SizedBox(
        width: 32,
        child: Icon(_iconFor(event.kind), semanticLabel: event.kind),
      ),
      title: Text(
        address == null || address.isEmpty ? _kindName(event.kind) : address,
      ),
      subtitle: body == null || body.isEmpty ? null : Text(body),
      trailing: Text(_eventTime(event)),
    );
  }

  static IconData _iconFor(String kind) => switch (kind) {
    'sms' || 'message' => Icons.message_outlined,
    'call' => Icons.call_outlined,
    'notification' => Icons.notifications_outlined,
    _ => Icons.circle_outlined,
  };

  static String _kindName(String kind) => switch (kind) {
    'sms' || 'message' => 'Message',
    'call' => 'Call',
    'notification' => 'Notification',
    _ => kind,
  };

  static String _eventTime(GatewayEvent event) {
    // Prefer the device clock. The host's receivedAt is only a fallback;
    // mixing both clocks across otherwise complete rows makes the feed jump.
    final seconds = event.timestamp ?? event.receivedAt;
    if (seconds == null) return 'Time unavailable';
    final time = DateTime.fromMillisecondsSinceEpoch(
      seconds * Duration.millisecondsPerSecond,
    );
    String two(int value) => value.toString().padLeft(2, '0');
    return '${two(time.hour)}:${two(time.minute)}';
  }
}
