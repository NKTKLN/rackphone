import 'package:flutter/material.dart';

import '../../api/gateway_client.dart';
import '../../api/models.dart';

/// Combines live unit values with gateway-owned storage and security facts.
class OverviewPage extends StatefulWidget {
  const OverviewPage({required this.gateway, required this.unit, super.key});

  final GatewayApi gateway;
  final RackUnit unit;

  @override
  State<OverviewPage> createState() => _OverviewPageState();
}

class _OverviewPageState extends State<OverviewPage> {
  UnitTelemetry? _telemetry;
  GatewayStats? _stats;
  Object? _telemetryFailure;
  Object? _statsFailure;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    if (mounted) setState(() => _loading = true);
    UnitTelemetry? telemetry;
    Object? telemetryFailure;
    GatewayStats? stats;
    Object? statsFailure;

    // Telemetry is a USB round trip, so it runs only when this page appears or
    // the operator pulls to refresh. A timer would keep waking the phone.
    //
    // Each call is wrapped rather than awaited bare: this method is started
    // unawaited from initState, so anything that escaped here would arrive as
    // an unhandled error and take the screen down instead of filling in a
    // message. The two are independent - a refused telemetry call must still
    // leave the stored counts on screen.
    await Future.wait(<Future<void>>[
      () async {
        try {
          telemetry = await widget.gateway.telemetry(widget.unit.name);
        } catch (failure) {
          telemetryFailure = failure;
        }
      }(),
      () async {
        try {
          stats = await widget.gateway.stats();
        } catch (failure) {
          statsFailure = failure;
        }
      }(),
    ]);

    if (!mounted) return;
    setState(() {
      _telemetry = telemetry;
      _telemetryFailure = telemetryFailure;
      _stats = stats;
      _statsFailure = statsFailure;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) => RefreshIndicator(
    onRefresh: _refresh,
    child: ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      children: <Widget>[
        _Section(title: 'Live values', child: _liveValues()),
        const SizedBox(height: 16),
        _Section(title: 'Stored', child: _stored()),
        const SizedBox(height: 16),
        _Section(title: 'Security', child: _security()),
      ],
    ),
  );

  Widget _liveValues() {
    final telemetry = _telemetry;
    if (_loading && telemetry == null) {
      return const LinearProgressIndicator();
    }
    if (_telemetryFailure != null) {
      return Text(
        'Live values for ${widget.unit.name} could not be loaded. Pull to refresh and try again.',
      );
    }
    if (telemetry == null) {
      return Text(
        'No live values were returned for ${widget.unit.name}. Pull to refresh and try again.',
      );
    }
    if (!telemetry.up) {
      return Text(
        '${widget.unit.name} did not answer. Asked ${_dateTime(telemetry.collectedAt)}. Pull to refresh to ask again.',
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text('${widget.unit.name} answered'),
        Text('Battery: ${_number(telemetry.batteryPercent, suffix: '%')}'),
        Text(
          'Temperature: ${_number(telemetry.skinTemperature ?? telemetry.batteryTemperature, suffix: ' °C')}',
        ),
        Text('Uptime: ${_duration(telemetry.uptime)}'),
      ],
    );
  }

  Widget _stored() {
    final stats = _stats;
    if (_loading && stats == null) return const LinearProgressIndicator();
    if (_statsFailure != null || stats == null) {
      return const Text(
        'Stored counts could not be loaded. Pull to refresh and try again.',
      );
    }
    if (stats.eventsByKind.isEmpty) {
      return const Text('No events are stored yet.');
    }
    final entries = stats.eventsByKind.entries.toList()
      ..sort((a, b) => a.key.compareTo(b.key));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: entries
          .map((entry) => Text('${_kind(entry.key)}: ${entry.value}'))
          .toList(growable: false),
    );
  }

  Widget _security() {
    final stats = _stats;
    if (_loading && stats == null) return const LinearProgressIndicator();
    if (_statsFailure != null || stats == null) {
      return const Text(
        'The security summary could not be loaded. Pull to refresh and try again.',
      );
    }
    final security = stats.security;
    if (security == null) {
      return const Text('The gateway did not provide a security summary.');
    }
    final login = security.lastLoginAt == null
        ? 'No login has been recorded'
        : 'Last login: ${_dateTime(security.lastLoginAt!)}${security.lastLoginDevice == null ? '' : ' from ${security.lastLoginDevice}'}';
    final now = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    final locked = security.lockedUntil != null && security.lockedUntil! > now;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(login),
        Text('Failed attempts in the last day: ${security.failedLogins24h}'),
        Text(
          locked
              ? 'Lockout in force until ${_dateTime(security.lockedUntil!)}'
              : 'No lockout is in force',
        ),
        Text(security.totpEnabled ? 'TOTP is on' : 'TOTP is off'),
        if (!security.totpEnabled)
          const Text(
            'This gateway is reachable from the internet, so a password is the only barrier to access.',
          ),
      ],
    );
  }

  static String _number(double? value, {required String suffix}) {
    if (value == null) return 'Unavailable';
    final text = value == value.roundToDouble()
        ? value.toInt().toString()
        : value.toStringAsFixed(1);
    return '$text$suffix';
  }

  static String _duration(double? seconds) {
    if (seconds == null) return 'Unavailable';
    final duration = Duration(seconds: seconds.round());
    final hours = duration.inHours;
    final minutes = duration.inMinutes.remainder(60);
    return '${hours}h ${minutes}m';
  }

  static String _kind(String value) => switch (value) {
    'sms' => 'Messages',
    'call' => 'Calls',
    'notification' => 'Notifications',
    _ => value,
  };

  static String _dateTime(int seconds) {
    final value = DateTime.fromMillisecondsSinceEpoch(
      seconds * Duration.millisecondsPerSecond,
    );
    String two(int part) => part.toString().padLeft(2, '0');
    return '${value.year}-${two(value.month)}-${two(value.day)} ${two(value.hour)}:${two(value.minute)}';
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          child,
        ],
      ),
    ),
  );
}
