import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/gateway_client.dart';
import '../api/models.dart';

/// Owns a bounded query plus live tail for one unit's feed.
final class FeedController extends ChangeNotifier {
  factory FeedController({required GatewayApi gateway, required String unit}) =>
      FeedController._(gateway, unit);

  FeedController._(this._gateway, this.unit);

  static const int _maximumEvents = 500;

  final GatewayApi _gateway;
  final String unit;
  List<GatewayEvent> _events = const [];
  bool _loading = false;
  Object? _failure;
  String? _kind;
  StreamSubscription<GatewayEvent>? _subscription;

  List<GatewayEvent> get events => _events;
  bool get loading => _loading;
  Object? get failure => _failure;

  Future<void> load({String? kind}) async {
    _kind = kind;
    _loading = true;
    _failure = null;
    notifyListeners();
    try {
      final events = await _gateway.events(unit: unit, kind: kind);
      _events = _boundedUnique(events);
    } catch (failure) {
      // A failed feed is state because throwing would take its screen down.
      _failure = failure;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  Future<void> refresh() => load(kind: _kind);

  void listen() {
    if (_subscription != null) return;
    _subscription = _gateway.stream().listen(
      (event) {
        if (event.unit != unit) return;
        // Query and stream tail the same table, so ids remove their overlap.
        _events = _boundedUnique([event, ..._events]);
        notifyListeners();
      },
      onError: (Object failure) {
        // A dropped connection must leave the list the operator was reading.
        _failure = failure;
        // And it must not leave the tail dead: a subscription kept here after
        // its stream ended would turn every later `listen` into a silent
        // no-op, so the feed would stop updating until the screen was rebuilt.
        unawaited(stop());
        notifyListeners();
      },
      onDone: () => unawaited(stop()),
    );
  }

  Future<void> stop() async {
    final subscription = _subscription;
    _subscription = null;
    await subscription?.cancel();
  }

  List<GatewayEvent> _boundedUnique(Iterable<GatewayEvent> candidates) {
    final ids = <int>{};
    final unique = <GatewayEvent>[];
    for (final event in candidates) {
      if (ids.add(event.id)) unique.add(event);
      // A long-running session is capped so memory cannot grow without bound.
      if (unique.length == _maximumEvents) break;
    }
    return List.unmodifiable(unique);
  }

  @override
  void dispose() {
    unawaited(stop());
    super.dispose();
  }
}
