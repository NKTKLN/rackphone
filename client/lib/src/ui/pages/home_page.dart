import 'package:flutter/material.dart';

import '../../data/contact_book.dart';
import '../../data/inbox_controller.dart';
import '../../data/unit_status_controller.dart';
import '../tiles.dart';
import '../widgets.dart';

/// The unit at a glance: live values, then the newest messages and calls.
class HomePage extends StatelessWidget {
  const HomePage({
    required this.status,
    required this.inbox,
    required this.onOpen,
    required this.onOpenThread,
    this.contacts,
    this.onCall,
    super.key,
  });

  final UnitStatusController status;
  final InboxController inbox;
  final ContactBook? contacts;

  /// What tapping a call does; null leaves the log read-only.
  final ValueChanged<String>? onCall;
  final ValueChanged<InboxKind> onOpen;
  final ValueChanged<MessageThread> onOpenThread;

  Future<void> _refresh() =>
      Future.wait(<Future<void>>[status.refresh(), inbox.refresh()]);

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: Listenable.merge(<Listenable?>[status, inbox, contacts]),
    builder: (context, _) {
      final label = contacts?.label ?? plainLabel;
      final threads = inbox.threads.take(2).toList(growable: false);
      final calls = inbox.calls.take(2).toList(growable: false);
      return RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.only(bottom: 16),
          children: <Widget>[
            _Tiles(status: status),
            if (inbox.offers(InboxKind.messages)) ...<Widget>[
              _header('Messages', InboxKind.messages),
              if (threads.isEmpty)
                _none(inbox.loading ? 'Loading…' : 'No messages yet')
              else
                for (final thread in threads)
                  ThreadTile(
                    thread: thread,
                    label: label,
                    unseen: thread.messages.any(inbox.isUnseen),
                    onTap: () => onOpenThread(thread),
                  ),
            ],
            if (inbox.offers(InboxKind.calls)) ...<Widget>[
              _header('Calls', InboxKind.calls),
              if (calls.isEmpty)
                _none(inbox.loading ? 'Loading…' : 'No calls yet')
              else
                for (final call in calls)
                  CallTile(
                    call: call,
                    label: label,
                    onTap: _caller(call.address),
                  ),
            ],
          ],
        ),
      );
    },
  );

  VoidCallback? _caller(String? address) {
    final call = onCall;
    if (call == null || address == null || address.isEmpty) return null;
    return () => call(address);
  }

  Widget _header(String title, InboxKind kind) => SectionHeader(
    title: title,
    action: TextButton(
      onPressed: () => onOpen(kind),
      child: const Text('See all'),
    ),
  );

  Widget _none(String text) => Builder(
    builder: (context) => Padding(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 8),
      child: Text(
        text,
        style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant),
      ),
    ),
  );
}

class _Tiles extends StatelessWidget {
  const _Tiles({required this.status});

  final UnitStatusController status;

  @override
  Widget build(BuildContext context) {
    final telemetry = status.telemetry;
    final live = telemetry != null && telemetry.up && status.failure == null;
    String value(double? number, String unit) =>
        !live || number == null ? '—' : '${number.round()}$unit';
    final temperature =
        telemetry?.skinTemperature ?? telemetry?.batteryTemperature;
    final uptime = telemetry?.uptime;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
      child: Row(
        children: <Widget>[
          _Tile(label: 'Battery', value: value(telemetry?.batteryPercent, '%')),
          _Tile(label: 'Temperature', value: value(temperature, '°C')),
          _Tile(
            label: 'Uptime',
            value: !live || uptime == null ? '—' : _uptime(uptime),
          ),
        ],
      ),
    );
  }

  static String _uptime(double seconds) {
    final duration = Duration(seconds: seconds.round());
    if (duration.inDays > 0) return '${duration.inDays} d';
    if (duration.inHours > 0) return '${duration.inHours} h';
    return '${duration.inMinutes} min';
  }
}

class _Tile extends StatelessWidget {
  const _Tile({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Expanded(
      child: Card(
        margin: const EdgeInsets.all(4),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                label,
                style: theme.textTheme.labelMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 4),
              Text(value, style: theme.textTheme.headlineSmall),
            ],
          ),
        ),
      ),
    );
  }
}
