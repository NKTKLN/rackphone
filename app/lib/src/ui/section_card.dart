import 'package:flutter/material.dart';

/// A titled block. The screen is a stack of these, in the order a unit is set
/// up: can it send, which SIM, what schedule, how the host reaches it.
class SectionCard extends StatelessWidget {
  const SectionCard({
    required this.title,
    required this.children,
    this.subtitle,
    this.trailing,
    super.key,
  });

  final String title;
  final String? subtitle;
  final Widget? trailing;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(title, style: theme.textTheme.titleMedium),
                      if (subtitle != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Text(
                            subtitle!,
                            style: theme.textTheme.bodySmall?.copyWith(
                              color: theme.colorScheme.onSurfaceVariant,
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
                ?trailing,
              ],
            ),
            const SizedBox(height: 12),
            ...children,
          ],
        ),
      ),
    );
  }
}

/// One fact and its value, for the lines nobody edits.
class FactRow extends StatelessWidget {
  const FactRow(this.label, this.value, {this.tone, super.key});

  final String label;
  final String value;

  /// Null leaves the value in the default colour: most facts are neither good
  /// nor bad, and colouring all of them would stop the ones that matter from
  /// standing out.
  final bool? tone;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colour = switch (tone) {
      true => theme.colorScheme.primary,
      false => theme.colorScheme.error,
      null => theme.colorScheme.onSurface,
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          const SizedBox(width: 12),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.end,
              style: theme.textTheme.bodyMedium?.copyWith(color: colour),
            ),
          ),
        ],
      ),
    );
  }
}
