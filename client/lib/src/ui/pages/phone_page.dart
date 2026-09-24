import 'package:flutter/material.dart';

import '../../api/models.dart';
import '../../data/contact_book.dart';
import '../../data/inbox_controller.dart';
import '../tiles.dart';
import '../widgets.dart';

/// The call log and the address book, as two tabs.
class PhonePage extends StatelessWidget {
  const PhonePage({
    required this.inbox,
    this.contacts,
    this.onMessage,
    this.onCall,
    super.key,
  });

  final InboxController inbox;
  final ContactBook? contacts;

  /// Opens a conversation with an address; null hides the action.
  final ValueChanged<String>? onMessage;

  /// Calls an address; null leaves the page read-only.
  final ValueChanged<String>? onCall;

  @override
  Widget build(BuildContext context) {
    final contacts = this.contacts;
    final recents = _Recents(inbox: inbox, contacts: contacts, onCall: onCall);
    if (contacts == null) return recents;
    return DefaultTabController(
      length: 2,
      child: Column(
        children: <Widget>[
          const TabBar(
            tabs: <Widget>[
              Tab(text: 'Recents'),
              Tab(text: 'Contacts'),
            ],
          ),
          Expanded(
            child: TabBarView(
              children: <Widget>[
                recents,
                ContactsList(
                  contacts: contacts,
                  onMessage: onMessage,
                  onCall: onCall,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Recents extends StatelessWidget {
  const _Recents({required this.inbox, this.contacts, this.onCall});

  final InboxController inbox;
  final ContactBook? contacts;
  final ValueChanged<String>? onCall;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: Listenable.merge(<Listenable?>[inbox, contacts]),
    builder: (context, _) {
      final calls = inbox.calls;
      return RefreshIndicator(
        onRefresh: inbox.refresh,
        child: calls.isEmpty
            ? EmptyState(
                text: inbox.loading
                    ? 'Loading…'
                    : inbox.failure != null
                    ? 'Calls could not be loaded. Pull to refresh.'
                    : 'Calls to ${inbox.unit} will appear here.',
              )
            : ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                itemCount: calls.length,
                itemBuilder: (context, index) {
                  final call = calls[index];
                  final address = call.address;
                  final dial = onCall;
                  return CallTile(
                    call: call,
                    label: contacts?.label ?? plainLabel,
                    onTap: dial == null || address == null || address.isEmpty
                        ? null
                        : () => dial(address),
                  );
                },
              ),
      );
    },
  );
}

/// The unit's address book under letter headings, with an index down the
/// side that jumps to a letter.
class ContactsList extends StatefulWidget {
  const ContactsList({
    required this.contacts,
    this.onMessage,
    this.onCall,
    super.key,
  });

  final ContactBook contacts;
  final ValueChanged<String>? onMessage;
  final ValueChanged<String>? onCall;

  @override
  State<ContactsList> createState() => _ContactsListState();
}

class _ContactsListState extends State<ContactsList> {
  // Fixed heights make a letter's offset a sum, so the index can jump without
  // laying out everything above it.
  static const double _headerHeight = 36;
  static const double _rowHeight = 64;

  final _scroll = ScrollController();

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  static String _letter(Contact contact) {
    final first = contact.name.trim().characters.firstOrNull ?? '#';
    return RegExp(r'\p{L}', unicode: true).hasMatch(first)
        ? first.toUpperCase()
        : '#';
  }

  Future<void> _openContact(Contact contact) async {
    final message = widget.onMessage;
    final call = widget.onCall;
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            ListTile(
              leading: Avatar(name: contact.name),
              title: Text(contact.name),
              subtitle: Text(contact.number),
            ),
            if (message != null)
              ListTile(
                leading: const Icon(Icons.chat_outlined),
                title: const Text('Send message'),
                onTap: () {
                  Navigator.of(context).pop();
                  message(contact.address);
                },
              ),
            if (call != null)
              ListTile(
                leading: const Icon(Icons.call_outlined),
                title: const Text('Call'),
                onTap: () {
                  Navigator.of(context).pop();
                  call(contact.address);
                },
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.contacts,
    builder: (context, _) {
      final book = widget.contacts;
      final contacts = book.contacts;
      if (contacts.isEmpty) {
        return RefreshIndicator(
          onRefresh: () => book.load(refresh: true),
          child: EmptyState(
            text: book.loading
                ? 'Loading…'
                : book.failure != null
                ? 'Contacts could not be read from ${book.unit}. The companion '
                      'app needs the contacts permission.'
                : 'The address book on ${book.unit} is empty.',
          ),
        );
      }
      final rows = <Object>[];
      final offsets = <String, double>{};
      var offset = 0.0;
      String? current;
      for (final contact in contacts) {
        final letter = _letter(contact);
        if (letter != current) {
          current = letter;
          offsets[letter] = offset;
          rows.add(letter);
          offset += _headerHeight;
        }
        rows.add(contact);
        offset += _rowHeight;
      }
      return Stack(
        children: <Widget>[
          RefreshIndicator(
            onRefresh: () => book.load(refresh: true),
            child: ListView.builder(
              controller: _scroll,
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(right: 24, bottom: 88),
              itemCount: rows.length,
              itemBuilder: (context, index) => switch (rows[index]) {
                final String letter => _Heading(
                  letter: letter,
                  height: _headerHeight,
                ),
                final Contact contact => SizedBox(
                  height: _rowHeight,
                  child: ListTile(
                    leading: Avatar(name: contact.name),
                    title: Text(
                      contact.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(contact.number),
                    onTap: () => _openContact(contact),
                  ),
                ),
                _ => const SizedBox.shrink(),
              },
            ),
          ),
          Positioned(
            right: 0,
            top: 0,
            bottom: 0,
            child: _LetterIndex(
              letters: offsets.keys.toList(growable: false),
              onLetter: (letter) {
                if (!_scroll.hasClients) return;
                final target = offsets[letter]!.clamp(
                  0.0,
                  _scroll.position.maxScrollExtent,
                );
                _scroll.jumpTo(target);
              },
            ),
          ),
        ],
      );
    },
  );
}

class _Heading extends StatelessWidget {
  const _Heading({required this.letter, required this.height});

  final String letter;
  final double height;

  @override
  Widget build(BuildContext context) => SizedBox(
    height: height,
    child: Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 16, 0),
      child: Text(
        letter,
        style: Theme.of(context).textTheme.labelLarge?.copyWith(
          color: Theme.of(context).colorScheme.primary,
        ),
      ),
    ),
  );
}

/// The letters down the side; a tap or a drag lands on the nearest one.
class _LetterIndex extends StatelessWidget {
  const _LetterIndex({required this.letters, required this.onLetter});

  final List<String> letters;
  final ValueChanged<String> onLetter;

  @override
  Widget build(BuildContext context) {
    if (letters.length < 2) return const SizedBox.shrink();
    final style = Theme.of(context).textTheme.labelSmall?.copyWith(
      color: Theme.of(context).colorScheme.primary,
    );
    return LayoutBuilder(
      builder: (context, constraints) {
        void pick(double dy) {
          final slot = constraints.maxHeight / letters.length;
          final index = (dy / slot).floor().clamp(0, letters.length - 1);
          onLetter(letters[index]);
        }

        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTapDown: (details) => pick(details.localPosition.dy),
          onVerticalDragUpdate: (details) => pick(details.localPosition.dy),
          child: SizedBox(
            width: 24,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: <Widget>[
                for (final letter in letters)
                  Text(letter, style: style, semanticsLabel: 'Jump to $letter'),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// App notifications collected on the unit, newest first.
class NotificationsPage extends StatelessWidget {
  const NotificationsPage({required this.inbox, super.key});

  final InboxController inbox;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: inbox,
    builder: (context, _) {
      final notifications = inbox.notifications;
      return RefreshIndicator(
        onRefresh: inbox.refresh,
        child: notifications.isEmpty
            ? EmptyState(
                text: inbox.loading
                    ? 'Loading…'
                    : inbox.failure != null
                    ? 'Notifications could not be loaded. Pull to refresh.'
                    : 'App notifications on ${inbox.unit} will appear here.',
              )
            : ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                itemCount: notifications.length,
                itemBuilder: (context, index) {
                  final notification = notifications[index];
                  return NotificationTile(
                    notification: notification,
                    unseen: inbox.isUnseen(notification),
                  );
                },
              ),
      );
    },
  );
}
