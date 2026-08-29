import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../control.dart';
import '../models.dart';
import 'section_card.dart';

/// The setup screen.
///
/// Everything here is also a broadcast, because a racked unit has nobody to tap
/// it. What the screen adds is the ten minutes before it is racked: proving the
/// permission is granted, that a SIM is visible, and that a message actually
/// leaves - while the phone is still on a desk and a mistake is cheap.
class HomePage extends StatefulWidget {
  const HomePage({required this.control, super.key});

  final CompanionControl control;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> with WidgetsBindingObserver {
  final TextEditingController _target = TextEditingController();
  final TextEditingController _interval = TextEditingController();
  final TextEditingController _body = TextEditingController();
  final GlobalKey<FormState> _form = GlobalKey<FormState>();

  CompanionStatus? _status;
  List<SendRecord> _recent = const <SendRecord>[];
  String _token = '';
  bool _tokenVisible = false;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _target.dispose();
    _interval.dispose();
    _body.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // A permission dialog and the host both change state behind the screen's
    // back, so coming back into the foreground is always a reason to re-read.
    if (state == AppLifecycleState.resumed) _refresh();
  }

  Future<void> _refresh() async {
    await _run(() async {
      final status = await widget.control.status();
      final recent = await widget.control.recent(limit: 12);
      final token = await widget.control.token();
      if (!mounted) return;
      setState(() {
        _status = status;
        _recent = recent;
        _token = token;
        // Only adopt values into the fields the person is not editing: a
        // background refresh must never overwrite half-typed input.
        if (!_focusedOnForm) {
          _target.text = status.keepalive.to;
          _interval.text = '${status.keepalive.intervalHours}';
        }
      });
    });
  }

  bool get _focusedOnForm =>
      FocusManager.instance.primaryFocus?.hasFocus ?? false;

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
    } on PlatformException catch (error) {
      if (mounted) setState(() => _error = error.message ?? error.code);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _say(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _saveKeepalive({bool? enabled}) async {
    if (!(_form.currentState?.validate() ?? false)) return;
    await _run(() async {
      final status = await widget.control.setConfig(
        keepaliveEnabled: enabled,
        keepaliveTo: _target.text.trim(),
        keepaliveIntervalHours: int.parse(_interval.text.trim()),
      );
      if (!mounted) return;
      setState(() => _status = status);
      _say('Schedule saved');
    });
  }

  Future<void> _keepaliveNow() async {
    await _run(() async {
      final record = await widget.control.keepaliveNow();
      _say(record == null ? 'Nothing was due' : _describe(record));
      await _refresh();
    });
  }

  Future<void> _sendTest(String to, String body) async {
    await _run(() async {
      final record = await widget.control.send(to: to, body: body);
      _say(_describe(record));
      await _refresh();
    });
  }

  String _describe(SendRecord record) => switch (record.status) {
    'queued' => 'Handed to the radio',
    'ok' => 'Delivered to the network',
    'rejected' => 'Refused: ${record.error}',
    'failed' => 'Failed: ${record.error}',
    _ => record.status,
  };

  @override
  Widget build(BuildContext context) {
    final status = _status;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Rackphone'),
        actions: <Widget>[
          IconButton(
            onPressed: _busy ? null : _refresh,
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
          ),
        ],
        bottom: _busy
            ? const PreferredSize(
                preferredSize: Size.fromHeight(2),
                child: LinearProgressIndicator(minHeight: 2),
              )
            : null,
      ),
      body: status == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _refresh,
              child: Form(
                key: _form,
                child: ListView(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  children: <Widget>[
                    if (_error != null) _errorBanner(_error!),
                    _readiness(status),
                    _sims(status),
                    _keepalive(status),
                    _manualSend(status),
                    _receiving(status),
                    _hostAccess(),
                    _activity(),
                    const SizedBox(height: 24),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _errorBanner(String message) =>
      SectionCard(title: 'Something failed', children: <Widget>[Text(message)]);

  Widget _readiness(CompanionStatus status) {
    final blockers = status.blockers;
    return SectionCard(
      title: status.ready ? 'Ready to send' : 'Not ready',
      subtitle: status.ready
          ? 'Permission granted, SIM present, token set.'
          : 'The unit cannot send until these are fixed.',
      trailing: Icon(
        status.ready ? Icons.check_circle : Icons.error,
        color: status.ready
            ? Theme.of(context).colorScheme.primary
            : Theme.of(context).colorScheme.error,
      ),
      children: <Widget>[
        for (final blocker in blockers)
          FactRow(blocker, 'blocked', tone: false),
        if (!status.canSendSms)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: FilledButton.icon(
              onPressed: _busy
                  ? null
                  : () => _run(() => widget.control.requestPermissions()),
              icon: const Icon(Icons.lock_open),
              label: const Text('Grant SMS permission'),
            ),
          ),
        FactRow('Sent', '${status.counters.sentOk}'),
        FactRow(
          'Failed',
          '${status.counters.sentFailed}',
          tone: status.counters.sentFailed > 0 ? false : null,
        ),
        FactRow(
          'Refused',
          '${status.counters.rejected}',
          tone: status.counters.rejected > 0 ? false : null,
        ),
        if (status.counters.pending > 0)
          FactRow('In flight', '${status.counters.pending}'),
      ],
    );
  }

  Widget _sims(CompanionStatus status) => SectionCard(
    title: 'SIM',
    subtitle: status.sims.isEmpty
        ? 'No subscription is readable. Grant READ_PHONE_STATE to choose one.'
        : 'Which subscription sends. Android decides when left on any.',
    children: <Widget>[
      RadioGroup<int>(
        groupValue: status.subId,
        onChanged: (int? value) {
          if (!_busy) unawaited(_pickSim(value));
        },
        child: Column(
          children: <Widget>[
            const RadioListTile<int>(
              value: -1,
              title: Text('Any (system default)'),
              contentPadding: EdgeInsets.zero,
            ),
            for (final sim in status.sims)
              RadioListTile<int>(
                value: sim.subId,
                title: Text(sim.title),
                subtitle: Text(_simSubtitle(status, sim)),
                contentPadding: EdgeInsets.zero,
              ),
          ],
        ),
      ),
      const SizedBox(height: 4),
      OutlinedButton.icon(
        onPressed: _busy ? null : _checkBalance,
        icon: const Icon(Icons.account_balance_wallet_outlined),
        label: const Text('Check balance'),
      ),
    ],
  );

  /// The number, and the money behind it. Balance is the failure the keepalive
  /// says nothing about: a SIM nobody reclaimed but that cannot send either.
  String _simSubtitle(CompanionStatus status, SimInfo sim) {
    final number = sim.number.isEmpty ? 'number not on the SIM' : sim.number;
    final balance = status.balanceFor(sim.subId);
    if (balance == null) return number;
    return '$number · ${balance.label} '
        '(${describeWhen(balance.checkedMs, now: DateTime.now())})';
  }

  Future<void> _checkBalance() async {
    await _run(() async {
      final readings = await widget.control.checkBalance();
      if (readings.isEmpty) {
        _say('No SIM answered');
      } else {
        _say(readings.map((BalanceReading r) => r.label).join(' · '));
      }
      await _refresh();
    });
  }

  /// Name a subscription the way the SIM card above does, so the two lists
  /// read as the same device.
  String _simLabel(CompanionStatus status, int subId) {
    for (final sim in status.sims) {
      if (sim.subId == subId) return sim.title;
    }
    return subId < 0 ? 'Default SIM' : 'SIM $subId';
  }

  Future<void> _setScope(bool everySim) async {
    await _run(() async {
      final status = await widget.control.setConfig(
        keepaliveSubs: everySim ? subsAll : subsDefault,
      );
      if (mounted) setState(() => _status = status);
    });
  }

  Future<void> _pickSim(int? value) async {
    if (value == null) return;
    await _run(() async {
      final status = await widget.control.setConfig(subId: value);
      if (mounted) setState(() => _status = status);
    });
  }

  Widget _keepalive(CompanionStatus status) {
    final keepalive = status.keepalive;
    return SectionCard(
      title: 'Keepalive',
      subtitle:
          'One message on a schedule, so the operator does not '
          'reclaim a SIM that never sends anything.',
      trailing: Switch(
        value: keepalive.enabled,
        onChanged: _busy ? null : (bool on) => _saveKeepalive(enabled: on),
      ),
      children: <Widget>[
        TextFormField(
          controller: _target,
          enabled: !_busy,
          keyboardType: TextInputType.phone,
          decoration: const InputDecoration(
            labelText: 'Send to',
            helperText: 'A number, or "self" for this unit\'s own SIM',
          ),
          validator: (String? value) => validateTarget(value ?? ''),
        ),
        const SizedBox(height: 12),
        TextFormField(
          controller: _interval,
          enabled: !_busy,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            labelText: 'Every',
            suffixText: 'hours',
          ),
          validator: (String? value) => validateIntervalHours(value ?? ''),
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          children: <Widget>[
            for (final preset in const <int>[24, 168, 720, 2160])
              ActionChip(
                label: Text(describeInterval(preset)),
                onPressed: _busy
                    ? null
                    : () => setState(() => _interval.text = '$preset'),
              ),
          ],
        ),
        const SizedBox(height: 12),
        SegmentedButton<bool>(
          segments: const <ButtonSegment<bool>>[
            ButtonSegment<bool>(value: true, label: Text('Every SIM')),
            ButtonSegment<bool>(value: false, label: Text('Default SIM')),
          ],
          selected: <bool>{keepalive.coversAllSims},
          onSelectionChanged: _busy
              ? null
              : (Set<bool> picked) => unawaited(_setScope(picked.first)),
        ),
        const SizedBox(height: 12),
        // One line per SIM. An operator only sees its own, so a single
        // aggregate would let the SIM that is quietly dying hide behind the
        // one the host uses daily.
        if (keepalive.targets.isEmpty)
          const FactRow('Covered SIMs', 'none')
        else
          for (final target in keepalive.targets)
            FactRow(
              _simLabel(status, target.subId),
              keepalive.enabled
                  ? (target.resolves
                        ? describeWhen(target.nextDueMs, now: DateTime.now())
                        : 'no number to text')
                  : 'disabled',
              tone: keepalive.enabled && !target.resolves ? false : null,
            ),
        if (keepalive.enabled && keepalive.unresolved.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              keepalive.isSelf
                  ? 'A SIM that does not carry its own number cannot text '
                        'itself. Give it a number instead.'
                  : 'That number is not usable.',
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        const SizedBox(height: 12),
        Row(
          children: <Widget>[
            Expanded(
              child: FilledButton(
                onPressed: _busy ? null : () => _saveKeepalive(),
                child: const Text('Save'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: OutlinedButton(
                onPressed: _busy ? null : _keepaliveNow,
                child: const Text('Send now'),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _manualSend(CompanionStatus status) => SectionCard(
    title: 'Send a message',
    subtitle:
        'The same path the host uses. Worth doing once, before the unit '
        'is racked and a failure is a silent one.',
    children: <Widget>[
      FilledButton.tonalIcon(
        onPressed: _busy || !status.canSendSms ? null : _openSendDialog,
        icon: const Icon(Icons.send),
        label: const Text('Compose'),
      ),
      if (status.lastSend != null)
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: FactRow(
            'Last attempt',
            '${status.lastSend!.status} · '
                '${describeWhen(status.lastSend!.ts, now: DateTime.now())}',
            tone: status.lastSend!.isFailure ? false : null,
          ),
        ),
    ],
  );

  Future<void> _openSendDialog() async {
    final to = TextEditingController();
    final body = TextEditingController();
    final formKey = GlobalKey<FormState>();

    final bool? send = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Send a message'),
        content: Form(
          key: formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              TextFormField(
                controller: to,
                keyboardType: TextInputType.phone,
                decoration: const InputDecoration(labelText: 'To'),
                validator: (String? value) =>
                    validateTarget(value ?? '', allowSelf: false),
              ),
              TextFormField(
                controller: body,
                maxLines: 3,
                decoration: const InputDecoration(labelText: 'Message'),
                validator: (String? value) =>
                    (value ?? '').trim().isEmpty ? 'Not empty' : null,
              ),
            ],
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              if (formKey.currentState?.validate() ?? false) {
                Navigator.of(context).pop(true);
              }
            },
            child: const Text('Send'),
          ),
        ],
      ),
    );

    if (send ?? false) await _sendTest(to.text.trim(), body.text);
    to.dispose();
    body.dispose();
  }

  Widget _receiving(CompanionStatus status) {
    final inbox = status.inbox;
    return SectionCard(
      title: 'Receiving',
      subtitle:
          'Arriving SMS and incoming calls are spooled here until the '
          'host drains them. Nothing is pushed from the phone.',
      children: <Widget>[
        SwitchListTile(
          value: inbox.collectSms,
          onChanged: _busy
              ? null
              : (bool on) => unawaited(_setCollection(collectSms: on)),
          title: const Text('Collect SMS'),
          contentPadding: EdgeInsets.zero,
        ),
        SwitchListTile(
          value: inbox.collectCalls,
          onChanged: _busy
              ? null
              : (bool on) => unawaited(_setCollection(collectCalls: on)),
          title: const Text('Collect calls'),
          subtitle: const Text('Missed and answered incoming'),
          contentPadding: EdgeInsets.zero,
        ),
        SwitchListTile(
          value: inbox.includeBody,
          onChanged: _busy
              ? null
              : (bool on) => unawaited(_setCollection(includeBody: on)),
          title: const Text('Include message bodies'),
          subtitle: const Text('Off relays sender and time only'),
          contentPadding: EdgeInsets.zero,
        ),
        const SizedBox(height: 8),
        FactRow('Waiting for the host', '${inbox.pending}'),
        if (inbox.dropped > 0)
          FactRow(
            'Dropped at the ${inbox.cap} cap',
            '${inbox.dropped}',
            tone: false,
          ),
      ],
    );
  }

  Future<void> _setCollection({
    bool? collectSms,
    bool? collectCalls,
    bool? includeBody,
  }) async {
    await _run(() async {
      final status = await widget.control.setConfig(
        collectSms: collectSms,
        collectCalls: collectCalls,
        includeBody: includeBody,
      );
      if (mounted) setState(() => _status = status);
    });
  }

  Widget _hostAccess() => SectionCard(
    title: 'Host access',
    subtitle:
        'Every command the host sends carries this token. It is also in '
        'files/rackphone/token, which only root can read.',
    children: <Widget>[
      SelectableText(
        _tokenVisible
            ? _token
            : '${_token.isEmpty ? '' : _token.substring(0, 4)}'
                  '${'•' * 12}',
        style: const TextStyle(fontFamily: 'monospace'),
      ),
      const SizedBox(height: 8),
      Wrap(
        spacing: 8,
        children: <Widget>[
          TextButton(
            onPressed: () => setState(() => _tokenVisible = !_tokenVisible),
            child: Text(_tokenVisible ? 'Hide' : 'Reveal'),
          ),
          TextButton(
            onPressed: _token.isEmpty
                ? null
                : () async {
                    await Clipboard.setData(ClipboardData(text: _token));
                    _say('Token copied');
                  },
            child: const Text('Copy'),
          ),
          TextButton(
            onPressed: _busy
                ? null
                : () => _run(() async {
                    final token = await widget.control.rotateToken();
                    if (mounted) setState(() => _token = token);
                    _say('Token rotated - update the host');
                  }),
            child: const Text('Rotate'),
          ),
        ],
      ),
    ],
  );

  Widget _activity() => SectionCard(
    title: 'Recent attempts',
    subtitle: 'Destinations and outcomes. Message bodies are never stored.',
    children: <Widget>[
      if (_recent.isEmpty)
        const Text('Nothing sent yet.')
      else
        for (final record in _recent)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: Icon(
              record.isFailure ? Icons.error_outline : Icons.check,
              size: 20,
              color: record.isFailure
                  ? Theme.of(context).colorScheme.error
                  : null,
            ),
            title: Text('${record.to} · ${record.status}'),
            subtitle: Text(
              <String>[
                record.source,
                describeWhen(record.ts, now: DateTime.now()),
                if (record.error.isNotEmpty) record.error,
              ].join(' · '),
            ),
          ),
    ],
  );
}
