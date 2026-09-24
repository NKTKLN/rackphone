import 'package:flutter/material.dart';

import '../api/models.dart';
import '../data/inbox_controller.dart';
import 'widgets.dart';

/// One conversation: who, the latest line, and whether any of it is new.
class ThreadTile extends StatelessWidget {
  const ThreadTile({
    required this.thread,
    required this.unseen,
    this.label = plainLabel,
    this.onTap,
    super.key,
  });

  final MessageThread thread;
  final bool unseen;
  final Labeler label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final weight = unseen ? FontWeight.w700 : null;
    final name = label(thread.address);
    final latest = thread.latest;
    return ListTile(
      onTap: onTap,
      leading: Avatar(name: name),
      title: Text(
        name,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontWeight: weight),
      ),
      subtitle: Text(
        '${latest.direction == 'out' ? 'You: ' : ''}${latest.body ?? ''}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          fontWeight: weight,
          color: unseen ? theme.colorScheme.onSurface : null,
        ),
      ),
      trailing: Text(
        shortTime(latest),
        style: theme.textTheme.labelSmall?.copyWith(
          fontWeight: weight,
          color: unseen ? theme.colorScheme.primary : null,
        ),
      ),
    );
  }
}

/// One call in the log, missed ones in the error colour.
class CallTile extends StatelessWidget {
  const CallTile({
    required this.call,
    this.label = plainLabel,
    this.onTap,
    super.key,
  });

  final GatewayEvent call;
  final Labeler label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final missed = call.direction == 'missed';
    final color = missed ? theme.colorScheme.error : null;
    final name = label(call.address);
    return ListTile(
      onTap: onTap,
      leading: Avatar(name: name),
      title: Text(
        name,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(color: color),
      ),
      subtitle: Row(
        children: <Widget>[
          Icon(callIcon(call), size: 16, color: color),
          const SizedBox(width: 6),
          Text(callSummary(call), style: TextStyle(color: color)),
        ],
      ),
      trailing: Text(shortTime(call), style: theme.textTheme.labelSmall),
    );
  }
}

/// One app notification: the app, then its title and text.
class NotificationTile extends StatelessWidget {
  const NotificationTile({
    required this.notification,
    required this.unseen,
    super.key,
  });

  final GatewayEvent notification;
  final bool unseen;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    // The label is what a person recognises; the package is the fallback
    // when the device could not name the app.
    final app = notification.app?.isNotEmpty == true
        ? notification.app!
        : notification.address ?? 'Notification';
    final lines = <String>[
      if (notification.title?.isNotEmpty == true) notification.title!,
      if (notification.body?.isNotEmpty == true) notification.body!,
    ];
    return ListTile(
      leading: Avatar(name: app),
      title: Text(
        app,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontWeight: unseen ? FontWeight.w700 : null),
      ),
      subtitle: lines.isEmpty
          ? null
          : Text(
              lines.join(' · '),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
      trailing: Text(
        shortTime(notification),
        style: theme.textTheme.labelSmall,
      ),
    );
  }
}
