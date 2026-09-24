import 'package:flutter/foundation.dart';

import '../api/gateway_client.dart';
import '../api/models.dart';

/// One unit's live values, shared by the top bar's status line and Home.
///
/// Telemetry is a USB round trip, so it is asked for when a unit is chosen and
/// when the operator pulls to refresh. A timer would keep waking the phone.
final class UnitStatusController extends ChangeNotifier {
  UnitStatusController({required this.gateway, required this.unit});

  final GatewayApi gateway;
  final String unit;
  UnitTelemetry? _telemetry;
  Object? _failure;
  bool _loading = false;
  bool _disposed = false;

  UnitTelemetry? get telemetry => _telemetry;
  Object? get failure => _failure;
  bool get loading => _loading;

  /// The line under the unit's name in the top bar.
  String get summary {
    final telemetry = _telemetry;
    if (_failure != null) return 'Unreachable';
    if (telemetry == null) return _loading ? 'Checking…' : 'Unknown';
    if (!telemetry.up) return 'Not answering';
    final battery = telemetry.batteryPercent;
    return battery == null ? 'Online' : 'Online · ${battery.round()}%';
  }

  Future<void> refresh() async {
    _loading = true;
    notifyListeners();
    try {
      _telemetry = await gateway.telemetry(unit);
      _failure = null;
    } catch (failure) {
      _failure = failure;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  /// A reply still in flight when the unit changes lands on a disposed
  /// controller, and must not reach listeners that are gone.
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
