import 'package:flutter/material.dart';

import '../../api/errors.dart';
import '../../api/models.dart';
import '../../data/contact_book.dart';
import '../../data/inbox_controller.dart';
import '../tiles.dart';
import '../widgets.dart';

/// Conversations, newest first, as Google Messages lists them.
class MessagesPage extends StatelessWidget {
  const MessagesPage({
    required this.inbox,
    required this.onOpenThread,
    this.contacts,
    super.key,
  });

  final InboxController inbox;
  final ValueChanged<MessageThread> onOpenThread;
  final ContactBook? contacts;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: Listenable.merge(<Listenable?>[inbox, contacts]),
    builder: (context, _) {
      final threads = inbox.threads;
      return RefreshIndicator(
        onRefresh: inbox.refresh,
        child: threads.isEmpty
            ? EmptyState(text: _emptyText())
            : ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                itemCount: threads.length,
                itemBuilder: (context, index) {
                  final thread = threads[index];
                  return ThreadTile(
                    thread: thread,
                    label: contacts?.label ?? plainLabel,
                    unseen: thread.messages.any(inbox.isUnseen),
                    onTap: () => onOpenThread(thread),
                  );
                },
              ),
      );
    },
  );

  String _emptyText() {
    if (inbox.loading) return 'Loading…';
    if (inbox.failure != null) {
      return 'Messages could not be loaded. Pull to refresh.';
    }
    return 'Text messages to ${inbox.unit} will appear here.';
  }
}

/// One conversation as bubbles, oldest at the top.
class ThreadPage extends StatefulWidget {
  const ThreadPage({
    required this.inbox,
    required this.address,
    this.contacts,
    super.key,
  });

  final InboxController inbox;
  final String address;
  final ContactBook? contacts;

  @override
  State<ThreadPage> createState() => _ThreadPageState();
}

class _ThreadPageState extends State<ThreadPage> {
  @override
  void initState() {
    super.initState();
    widget.inbox.addListener(_markSeen);
    WidgetsBinding.instance.addPostFrameCallback((_) => _markSeen());
  }

  @override
  void dispose() {
    widget.inbox.removeListener(_markSeen);
    super.dispose();
  }

  MessageThread? get _thread => widget.inbox.threadWith(widget.address);

  /// A message that arrives while its conversation is open is read already.
  void _markSeen() {
    final thread = _thread;
    if (thread != null) widget.inbox.markThreadSeen(thread);
  }

  /// The contact's name over the number, or just the number for a stranger.
  Widget _title() {
    final name = widget.contacts?.nameFor(widget.address);
    final number = plainLabel(widget.address);
    if (name == null) return Text(number);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Text(name),
        Text(number, style: Theme.of(context).textTheme.bodySmall),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: _title()),
    body: Column(
      children: <Widget>[
        Expanded(
          child: ListenableBuilder(
            listenable: widget.inbox,
            builder: (context, _) {
              final messages = _thread?.messages ?? const <GatewayEvent>[];
              return ListView.builder(
                reverse: true,
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 16,
                ),
                itemCount: messages.length,
                itemBuilder: (context, index) =>
                    _Bubble(message: messages[index]),
              );
            },
          ),
        ),
        if (widget.inbox.canSend && widget.address.isNotEmpty)
          Composer(onSend: (body) => widget.inbox.send(widget.address, body)),
      ],
    ),
  );
}

/// Starts a conversation with a number, then carries on in its thread.
class NewMessagePage extends StatefulWidget {
  const NewMessagePage({required this.inbox, super.key});

  final InboxController inbox;

  @override
  State<NewMessagePage> createState() => _NewMessagePageState();
}

class _NewMessagePageState extends State<NewMessagePage> {
  final _to = TextEditingController();

  @override
  void dispose() {
    _to.dispose();
    super.dispose();
  }

  Future<void> _send(String body) async {
    final to = _to.text.trim();
    if (!_destination.hasMatch(to)) {
      throw const FormatException('Enter a number: digits and an optional +.');
    }
    final sent = await widget.inbox.send(to, body);
    if (!mounted) return;
    // The device may have normalised the number, and the thread is keyed by
    // what it stored, so follow the stored address rather than the typed one.
    await Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) =>
            ThreadPage(inbox: widget.inbox, address: sent.address ?? to),
      ),
    );
  }

  /// The gateway's own rule; checking it here saves a round trip.
  static final _destination = RegExp(r'^\+?[0-9]+$');

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('New conversation')),
    body: Column(
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: TextField(
            controller: _to,
            autofocus: true,
            keyboardType: TextInputType.phone,
            decoration: const InputDecoration(
              labelText: 'To',
              hintText: '+7 900 000-00-00',
            ),
            // Spaces and dashes are how people write numbers; the gateway
            // accepts only digits, so they go before anything is checked.
            onChanged: (value) {
              final stripped = value.replaceAll(RegExp(r'[\s()-]'), '');
              if (stripped != value) {
                _to.value = TextEditingValue(
                  text: stripped,
                  selection: TextSelection.collapsed(offset: stripped.length),
                );
              }
            },
          ),
        ),
        const Spacer(),
        Composer(onSend: _send),
      ],
    ),
  );
}

/// The text field and send button under a conversation.
class Composer extends StatefulWidget {
  const Composer({required this.onSend, super.key});

  /// Completes when the message is accepted; throws to keep the text.
  final Future<void> Function(String body) onSend;

  @override
  State<Composer> createState() => _ComposerState();
}

class _ComposerState extends State<Composer> {
  final _text = TextEditingController();
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _text.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final body = _text.text.trim();
    if (body.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await widget.onSend(body);
      _text.clear();
    } catch (failure) {
      // The text stays in the field, so a failed send costs a tap, not a
      // retyped message.
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(sendFailureText(failure))));
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) => SafeArea(
    top: false,
    child: Padding(
      padding: const EdgeInsets.fromLTRB(12, 4, 8, 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: <Widget>[
          Expanded(
            child: TextField(
              controller: _text,
              enabled: !_sending,
              minLines: 1,
              maxLines: 5,
              textCapitalization: TextCapitalization.sentences,
              decoration: InputDecoration(
                hintText: 'Text message',
                filled: true,
                fillColor: Theme.of(context).colorScheme.surfaceContainerHigh,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 18,
                  vertical: 12,
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          IconButton.filled(
            tooltip: 'Send',
            onPressed: _text.text.trim().isEmpty || _sending ? null : _send,
            icon: _sending
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.send),
          ),
        ],
      ),
    ),
  );
}

/// Says why a send failed in words an operator can act on.
String sendFailureText(Object failure) => switch (failure) {
  FormatException(:final message) => message,
  GatewayForbiddenException() => 'This unit is not allowed to send messages.',
  GatewayNetworkException() => 'The gateway could not be reached.',
  GatewayProtocolException() => 'The unit could not send the message.',
  _ => 'The message was not sent.',
};

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message});

  final GatewayEvent message;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final mine = message.direction == 'out';
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.sizeOf(context).width * 0.78,
        ),
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 3),
          padding: const EdgeInsets.fromLTRB(14, 10, 14, 8),
          decoration: BoxDecoration(
            color: mine ? scheme.primaryContainer : scheme.surfaceContainerHigh,
            borderRadius: BorderRadius.circular(20),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              SelectableText(
                message.body ?? '',
                style: TextStyle(
                  color: mine ? scheme.onPrimaryContainer : scheme.onSurface,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                shortTime(message),
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: scheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
