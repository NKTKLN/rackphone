import 'package:flutter/foundation.dart';

import '../api/gateway_client.dart';
import '../api/models.dart';
import 'numbers.dart';

/// One unit's SIMs, and which of them a conversation should go out on.
final class SimBook extends ChangeNotifier {
  SimBook({required this.gateway, required this.unit});

  final GatewaySimsApi gateway;
  final String unit;
  UnitSims _sims = const UnitSims(sims: <Sim>[]);
  bool _disposed = false;

  List<Sim> get sims => _sims.sims;

  /// Whether there is anything to choose: with one SIM a picker is noise.
  bool get hasChoice => sims.length > 1;

  Future<void> load() async {
    try {
      _sims = await gateway.sims(unit);
    } catch (_) {
      // Without the list the unit still sends from its own default, which
      // is what it did before there was a choice, so a failure only hides it.
      _sims = const UnitSims(sims: <Sim>[]);
    }
    notifyListeners();
  }

  Sim? byId(int? subId) {
    if (subId == null) return null;
    for (final sim in sims) {
      if (sim.subId == subId) return sim;
    }
    return null;
  }

  /// The SIM to reach [address] on: whichever this number last used, so a
  /// reply comes from the number that was written to, else the unit's default.
  ///
  /// Null when there is no choice to make, which leaves it to the unit.
  int? preferredFor(String address, Iterable<GatewayEvent> history) {
    if (!hasChoice) return null;
    final key = numberKey(address);
    GatewayEvent? latest;
    for (final event in key.isEmpty ? const <GatewayEvent>[] : history) {
      if (byId(event.sub) == null || numberKey(event.address ?? '') != key) {
        continue;
      }
      if (latest == null || (event.timestamp ?? 0) > (latest.timestamp ?? 0)) {
        latest = event;
      }
    }
    return latest?.sub ?? byId(_sims.defaultSub)?.subId ?? sims.first.subId;
  }

  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
