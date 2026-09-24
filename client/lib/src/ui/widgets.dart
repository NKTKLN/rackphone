import 'package:flutter/material.dart';

import '../api/models.dart';

/// A contact's initial, or a person glyph for a bare number.
class Avatar extends StatelessWidget {
  const Avatar({required this.name, this.radius = 20, super.key});

  final String name;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final first = name.trim().isEmpty ? '' : name.trim()[0];
    final letter = RegExp(r'\p{L}', unicode: true).hasMatch(first);
    return CircleAvatar(
      radius: radius,
      backgroundColor: scheme.surfaceContainerHighest,
      foregroundColor: letter ? scheme.onSurface : scheme.onSurfaceVariant,
      child: letter
          ? Text(first.toUpperCase(), style: TextStyle(fontSize: radius * 0.8))
          : Icon(Icons.person, size: radius),
    );
  }
}

/// A section heading with an optional trailing action, as on Home.
class SectionHeader extends StatelessWidget {
  const SectionHeader({required this.title, this.action, super.key});

  final String title;
  final Widget? action;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(20, 16, 8, 0),
    child: Row(
      children: <Widget>[
        Expanded(
          child: Text(
            title,
            style: Theme.of(context).textTheme.titleSmall?.copyWith(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
        ),
        ?action,
      ],
    ),
  );
}

/// A centred sentence for an empty or failed list, kept pull-to-refreshable.
class EmptyState extends StatelessWidget {
  const EmptyState({required this.text, super.key});

  final String text;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) => SingleChildScrollView(
      physics: const AlwaysScrollableScrollPhysics(),
      child: ConstrainedBox(
        constraints: BoxConstraints(minHeight: constraints.maxHeight),
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Text(
              text,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

/// Turns an address into what a list shows for it.
typedef Labeler = String Function(String? address);

/// The address itself, for when no address book is at hand.
String plainLabel(String? address) =>
    address == null || address.isEmpty ? 'Unknown' : address;

/// Today's events show a clock time, this week's a weekday, older a date.
String shortTime(GatewayEvent event, {DateTime? now}) {
  final time = event.occurredAt;
  if (time == null) return '';
  final today = now ?? DateTime.now();
  final day = DateTime(time.year, time.month, time.day);
  final days = DateTime(today.year, today.month, today.day).difference(day);
  String two(int value) => value.toString().padLeft(2, '0');
  if (days.inDays == 0) return '${two(time.hour)}:${two(time.minute)}';
  if (days.inDays == 1) return 'Yesterday';
  if (days.inDays > 1 && days.inDays < 7) return _weekdays[time.weekday - 1];
  final date = '${_months[time.month - 1]} ${time.day}';
  return time.year == today.year ? date : '$date, ${time.year}';
}

/// "Incoming · 4 min", "Missed", and so on.
String callSummary(GatewayEvent call) {
  final seconds = call.duration ?? 0;
  final length = seconds <= 0
      ? ''
      : seconds < 60
      ? ' · $seconds s'
      : ' · ${(seconds / 60).round()} min';
  return switch (call.direction) {
    'missed' => 'Missed',
    'out' => 'Outgoing$length',
    _ => 'Incoming$length',
  };
}

IconData callIcon(GatewayEvent call) => switch (call.direction) {
  'missed' => Icons.call_missed,
  'out' => Icons.call_made,
  _ => Icons.call_received,
};

const _weekdays = <String>['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _months = <String>[
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];
