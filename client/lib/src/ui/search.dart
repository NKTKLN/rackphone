import 'package:flutter/material.dart';

import '../data/contact_book.dart';
import '../data/inbox_controller.dart';
import 'tiles.dart';
import 'widgets.dart';

/// Finds conversations by number or text, or calls by number.
///
/// It searches what is already loaded; the gateway has no search route.
class InboxSearch extends SearchDelegate<MessageThread?> {
  InboxSearch({required this.inbox, required this.calls, this.contacts})
    : super(searchFieldLabel: calls ? 'Search calls' : 'Search messages');

  final InboxController inbox;
  final bool calls;
  final ContactBook? contacts;

  @override
  ThemeData appBarTheme(BuildContext context) {
    final theme = Theme.of(context);
    return theme.copyWith(
      inputDecorationTheme: const InputDecorationTheme(
        border: InputBorder.none,
      ),
    );
  }

  @override
  List<Widget> buildActions(BuildContext context) => <Widget>[
    if (query.isNotEmpty)
      IconButton(
        tooltip: 'Clear',
        onPressed: () => query = '',
        icon: const Icon(Icons.close),
      ),
  ];

  @override
  Widget buildLeading(BuildContext context) =>
      BackButton(onPressed: () => close(context, null));

  @override
  Widget buildResults(BuildContext context) => _results(context);

  @override
  Widget buildSuggestions(BuildContext context) => _results(context);

  Widget _results(BuildContext context) {
    final needle = query.trim().toLowerCase();
    bool matches(String? text) => text?.toLowerCase().contains(needle) ?? false;
    final label = contacts?.label ?? plainLabel;
    // A number is found by the name saved for it as well as by its digits.
    bool matchesAddress(String? address) =>
        matches(address) || matches(contacts?.nameFor(address));
    if (calls) {
      final found = inbox.calls
          .where((call) => needle.isEmpty || matchesAddress(call.address))
          .toList(growable: false);
      return ListView.builder(
        itemCount: found.length,
        itemBuilder: (context, index) =>
            CallTile(call: found[index], label: label),
      );
    }
    final found = inbox.threads
        .where(
          (thread) =>
              needle.isEmpty ||
              matchesAddress(thread.address) ||
              thread.messages.any((message) => matches(message.body)),
        )
        .toList(growable: false);
    return ListView.builder(
      itemCount: found.length,
      itemBuilder: (context, index) {
        final thread = found[index];
        return ThreadTile(
          thread: thread,
          label: label,
          unseen: thread.messages.any(inbox.isUnseen),
          onTap: () => close(context, thread),
        );
      },
    );
  }
}
