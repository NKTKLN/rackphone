import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/errors.dart';
import '../api/gateway_client.dart';
import '../api/models.dart';
import 'numbers.dart';

enum InboxKind { messages, calls, notifications }

/// Every message exchanged with one address, newest first.
final class MessageThread {
  const MessageThread({required this.address, required this.messages});

  final String address;
  final List<GatewayEvent> messages;

  GatewayEvent get latest => messages.first;
}

/// Owns one unit's messages, calls and app notifications, and which of them
/// the operator has not looked at yet.
///
/// Each kind is queried on its own. A mixed query shares one row limit, and
/// notifications arrive in hundreds, so they would push every message and
/// call out of the page. One stream feeds all three afterwards.
final class InboxController extends ChangeNotifier {
  InboxController({required this.gateway, required RackUnit unit})
    : unit = unit.name,
      _kinds = <InboxKind>{
        if (unit.can('sms')) ...<InboxKind>[
          InboxKind.messages,
          InboxKind.calls,
        ],
        if (unit.can('notifications')) InboxKind.notifications,
      };

  static const int _queryLimit = 200;
  static const int _maximumPerKind = 500;

  final GatewayApi gateway;
  final String unit;
  final Set<InboxKind> _kinds;
  final Map<InboxKind, List<GatewayEvent>> _events = {
    for (final kind in InboxKind.values) kind: const <GatewayEvent>[],
  };

  /// Anything at or below a kind's baseline counts as seen. History that was
  /// already there when the app opened is not news, so a load moves it.
  final Map<InboxKind, int> _baseline = {
    for (final kind in InboxKind.values) kind: 0,
  };
  final Set<int> _seen = <int>{};
  bool _loading = false;
  bool _loadedOnce = false;
  bool _disposed = false;
  Object? _failure;
  StreamSubscription<GatewayEvent>? _subscription;
  Timer? _reconnect;
  int _retrySeconds = _firstRetrySeconds;

  static const int _firstRetrySeconds = 2;
  static const int _lastRetrySeconds = 60;

  bool get loading => _loading;
  Object? get failure => _failure;
  bool offers(InboxKind kind) => _kinds.contains(kind);

  List<GatewayEvent> get messages => _events[InboxKind.messages]!;
  List<GatewayEvent> get calls => _events[InboxKind.calls]!;
  List<GatewayEvent> get notifications => _events[InboxKind.notifications]!;

  /// Conversations, newest first, each under the address it last used.
  ///
  /// A reply from +7… to a message sent to 8… is the same conversation, so
  /// they are grouped as the contact book matches numbers.
  List<MessageThread> get threads {
    final byNumber = <String, List<GatewayEvent>>{};
    for (final message in messages) {
      byNumber
          .putIfAbsent(conversationKey(message.address), () => [])
          .add(message);
    }
    return byNumber.values
        .map(
          (thread) => MessageThread(
            address: thread.first.address ?? '',
            messages: List.unmodifiable(thread),
          ),
        )
        .toList(growable: false);
  }

  /// The conversation with [address], however that number is spelled.
  MessageThread? threadWith(String address) {
    final key = conversationKey(address);
    for (final thread in threads) {
      if (conversationKey(thread.address) == key) return thread;
    }
    return null;
  }

  bool isUnseen(GatewayEvent event) {
    final kind = _kindOf(event);
    if (kind == null || event.direction == 'out') return false;
    if (kind == InboxKind.calls && event.direction != 'missed') return false;
    return event.id > _baseline[kind]! && !_seen.contains(event.id);
  }

  int unseen(InboxKind kind) => _events[kind]!.where(isUnseen).length;

  void markSeen(InboxKind kind) {
    final events = _events[kind]!;
    if (events.isEmpty || !events.any(isUnseen)) return;
    _baseline[kind] = events.map((event) => event.id).reduce(_max);
    notifyListeners();
  }

  void markThreadSeen(MessageThread thread) {
    final before = _seen.length;
    _seen.addAll(thread.messages.where(isUnseen).map((event) => event.id));
    if (_seen.length != before) notifyListeners();
  }

  /// Whether this unit may send, and this gateway knows how.
  bool get canSend => offers(InboxKind.messages) && gateway.messaging != null;

  /// Sends one SMS and puts it in its conversation at once, rather than when
  /// the stream gets round to it; the stream's copy is the same id.
  Future<GatewayEvent> send(String to, String body) async {
    final messaging = gateway.messaging;
    if (messaging == null) {
      throw UnsupportedError('This gateway cannot send messages.');
    }
    final sent = await messaging.sendMessage(unit, to, body);
    _events[InboxKind.messages] = _boundedUnique(InboxKind.messages, [
      sent,
      ...messages,
    ]);
    notifyListeners();
    return sent;
  }

  Future<void> load() async {
    _loading = true;
    _failure = null;
    notifyListeners();
    try {
      await Future.wait(_kinds.map(_loadKind));
      if (!_loadedOnce) {
        _loadedOnce = true;
        for (final kind in InboxKind.values) {
          final events = _events[kind]!;
          if (events.isNotEmpty) {
            _baseline[kind] = events.map((event) => event.id).reduce(_max);
          }
        }
      }
    } catch (failure) {
      // A failed inbox is state because throwing would take its screen down.
      _failure = failure;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  Future<void> _loadKind(InboxKind kind) async {
    final queried = await gateway.events(
      unit: unit,
      kind: _gatewayKind(kind),
      limit: _queryLimit,
    );
    // Whatever the stream delivered while the query was in flight stays.
    _events[kind] = _boundedUnique(kind, [..._events[kind]!, ...queried]);
  }

  /// Follows the unit's live events, and comes back by itself after a drop.
  void listen() {
    _reconnect?.cancel();
    _reconnect = null;
    if (_subscription != null || _disposed) return;
    _subscription = gateway.stream().listen(
      (event) {
        _retrySeconds = _firstRetrySeconds;
        if (event.unit != unit) return;
        final kind = _kindOf(event);
        if (kind == null || !_kinds.contains(kind)) return;
        _events[kind] = _boundedUnique(kind, [event, ..._events[kind]!]);
        notifyListeners();
      },
      onError: (Object failure) {
        // A dropped connection keeps what is on screen and says so.
        _failure = failure;
        notifyListeners();
        _dropped(retry: failure is! GatewayAuthException);
      },
      onDone: () => _dropped(retry: true),
    );
  }

  /// Lets go of a dead stream and, unless the session is gone, tries again.
  ///
  /// Delays double from 2 to 60 seconds, as the notification service's do:
  /// a tight loop on a phone is both a battery drain and a request flood. The
  /// load after a reconnect picks up whatever arrived while it was down.
  void _dropped({required bool retry}) {
    unawaited(_cancelSubscription());
    if (!retry || _disposed) return;
    final delay = Duration(seconds: _retrySeconds);
    _retrySeconds = (_retrySeconds * 2).clamp(
      _firstRetrySeconds,
      _lastRetrySeconds,
    );
    _reconnect = Timer(delay, () {
      listen();
      unawaited(load());
    });
  }

  /// Reloads history and reconnects at once if the stream is down.
  Future<void> refresh() {
    listen();
    return load();
  }

  Future<void> stop() async {
    _reconnect?.cancel();
    _reconnect = null;
    await _cancelSubscription();
  }

  Future<void> _cancelSubscription() async {
    final subscription = _subscription;
    _subscription = null;
    await subscription?.cancel();
  }

  static InboxKind? _kindOf(GatewayEvent event) => switch (event.kind) {
    'sms' => InboxKind.messages,
    // A ringing call is the live call screen's business, not history.
    'call' when event.direction != 'ringing' => InboxKind.calls,
    'notification' => InboxKind.notifications,
    _ => null,
  };

  static String _gatewayKind(InboxKind kind) => switch (kind) {
    InboxKind.messages => 'sms',
    InboxKind.calls => 'call',
    InboxKind.notifications => 'notification',
  };

  List<GatewayEvent> _boundedUnique(
    InboxKind kind,
    List<GatewayEvent> candidates,
  ) {
    final ids = <int>{};
    final unique = <GatewayEvent>[];
    for (final event in candidates) {
      if (_kindOf(event) != kind) continue;
      if (ids.add(event.id)) unique.add(event);
    }
    unique.sort(_newestFirst);
    // A long-running session is capped so memory cannot grow without bound.
    return List.unmodifiable(unique.take(_maximumPerKind));
  }

  /// Ties fall back to the store's id so the order is stable.
  static int _newestFirst(GatewayEvent a, GatewayEvent b) {
    final byTime = (b.occurredAt?.millisecondsSinceEpoch ?? 0).compareTo(
      a.occurredAt?.millisecondsSinceEpoch ?? 0,
    );
    return byTime != 0 ? byTime : b.id.compareTo(a.id);
  }

  static int _max(int a, int b) => a > b ? a : b;

  /// A query still in flight when the unit changes lands on a disposed
  /// controller, and must not reach listeners that are gone.
  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(stop());
    super.dispose();
  }
}
