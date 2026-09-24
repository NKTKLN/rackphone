import 'dart:async';

import 'package:flutter/material.dart';

import '../../api/gateway_client.dart';
import '../../api/models.dart';
import '../../service/service_settings.dart';
import '../../session/session_controller.dart';
import '../../session/token_store.dart';
import '../widgets.dart';
import 'security_page.dart';

typedef LoadServiceSettings = Future<ServiceSettings> Function();
typedef SaveServiceSettings = Future<void> Function(ServiceSettings settings);

/// Notification policy, the account on this device, the gateway's security
/// posture, and versions.
class SettingsPage extends StatefulWidget {
  const SettingsPage({
    required this.session,
    this.loadSettings = ServiceSettings.read,
    SaveServiceSettings? saveSettings,
    super.key,
  }) : saveSettings = saveSettings ?? _save;

  final SessionController session;

  /// Injectable because the real store is a platform channel.
  final LoadServiceSettings loadSettings;
  final SaveServiceSettings saveSettings;

  static Future<void> _save(ServiceSettings settings) => settings.save();

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  ServiceSettings? _settings;
  StoredSession? _stored;
  GatewayStats? _stats;
  GatewayHealth? _health;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    final gateway = widget.session.gateway;
    // Each piece is independent; one refusal must leave the rest on screen.
    await Future.wait(<Future<void>>[
      _guard(() async => _settings = await widget.loadSettings()),
      _guard(() async => _stored = await widget.session.tokenStore.read()),
      if (gateway != null) ...<Future<void>>[
        _guard(() async => _stats = await gateway.stats()),
        _guard(() async => _health = await gateway.health()),
      ],
    ]);
    if (mounted) setState(() {});
  }

  Future<void> _guard(Future<void> Function() load) async {
    try {
      await load();
    } catch (_) {
      // Shown as unavailable rather than taking the page down.
    }
  }

  Future<void> _update(ServiceSettings settings) async {
    setState(() => _settings = settings);
    await widget.saveSettings(settings);
  }

  Future<void> _pickQuietHours() async {
    final current = _settings;
    if (current == null) return;
    final start = await showTimePicker(
      context: context,
      helpText: 'Quiet from',
      initialTime: _time(current.quietStartMinute ?? 23 * 60),
    );
    if (start == null || !mounted) return;
    final end = await showTimePicker(
      context: context,
      helpText: 'Quiet until',
      initialTime: _time(current.quietEndMinute ?? 7 * 60),
    );
    if (end == null) return;
    await _update(
      _copy(
        current,
        quietStartMinute: start.hour * 60 + start.minute,
        quietEndMinute: end.hour * 60 + end.minute,
      ),
    );
  }

  Future<void> _changeTotp(GatewayAdminApi admin, bool on) async {
    final changed = on
        ? await enableTotp(context, admin)
        : await disableTotp(context, admin);
    if (!changed) return;
    final gateway = widget.session.gateway;
    if (gateway == null) return;
    await _guard(() async => _stats = await gateway.stats());
    if (mounted) setState(() {});
  }

  Future<void> _signOut() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sign out?'),
        content: const Text(
          'This device stops receiving messages and calls until you sign in again.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Sign out'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    Navigator.of(context).popUntil((route) => route.isFirst);
    await widget.session.signOut();
  }

  @override
  Widget build(BuildContext context) {
    final settings = _settings;
    final stored = _stored;
    final security = _stats?.security;
    // Administration is offered only to a session that holds the scope: a
    // control session would be refused by every route behind it anyway.
    final admin = stored?.scope == 'admin'
        ? widget.session.gateway?.admin
        : null;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        children: <Widget>[
          const SectionHeader(title: 'Notifications'),
          if (settings == null)
            const ListTile(title: Text('Loading…'))
          else ...<Widget>[
            SwitchListTile(
              title: const Text('Messages'),
              value: settings.notifyOnSms,
              onChanged: (value) =>
                  _update(_copy(settings, notifyOnSms: value)),
            ),
            SwitchListTile(
              title: const Text('Calls'),
              value: settings.notifyOnCalls,
              onChanged: (value) =>
                  _update(_copy(settings, notifyOnCalls: value)),
            ),
            SwitchListTile(
              title: const Text('App notifications'),
              value: settings.notifyOnNotifications,
              onChanged: (value) =>
                  _update(_copy(settings, notifyOnNotifications: value)),
            ),
            ListTile(
              title: const Text('Quiet hours'),
              subtitle: Text(_quietHours(settings)),
              onTap: _pickQuietHours,
              trailing: settings.quietStartMinute == null
                  ? null
                  : IconButton(
                      tooltip: 'Turn off quiet hours',
                      icon: const Icon(Icons.close),
                      onPressed: () =>
                          _update(_copy(settings, clearQuiet: true)),
                    ),
            ),
          ],
          const Divider(),
          const SectionHeader(title: 'Account'),
          ListTile(
            title: const Text('Gateway'),
            subtitle: Text(stored?.baseUrl.toString() ?? '—'),
          ),
          ListTile(
            title: const Text('This device'),
            subtitle: Text(
              stored == null
                  ? '—'
                  : '${stored.deviceLabel} · ${stored.scope} access',
            ),
          ),
          ListTile(
            leading: const Icon(Icons.logout),
            title: const Text('Sign out'),
            onTap: _signOut,
          ),
          const Divider(),
          const SectionHeader(title: 'Security'),
          if (security == null)
            const ListTile(title: Text('Not available'))
          else ...<Widget>[
            ListTile(
              title: const Text('Last sign-in'),
              subtitle: Text(
                security.lastLoginAt == null
                    ? 'None recorded'
                    : '${_dateTime(security.lastLoginAt!)}'
                          '${security.lastLoginDevice == null ? '' : ' · ${security.lastLoginDevice}'}',
              ),
            ),
            ListTile(
              title: const Text('Failed attempts, last 24 hours'),
              subtitle: Text('${security.failedLogins24h}'),
            ),
            if (admin == null)
              ListTile(
                title: const Text('Two-factor authentication'),
                subtitle: Text(
                  security.totpEnabled
                      ? 'On'
                      : 'Off — a password is the only barrier to this gateway',
                ),
              )
            else
              SwitchListTile(
                title: const Text('Two-factor authentication'),
                subtitle: Text(
                  security.totpEnabled
                      ? 'On'
                      : 'Off — a password is the only barrier to this gateway',
                ),
                value: security.totpEnabled,
                onChanged: (on) => _changeTotp(admin, on),
              ),
          ],
          if (admin != null) ...<Widget>[
            ListTile(
              leading: const Icon(Icons.devices_outlined),
              title: const Text('Sessions'),
              subtitle: const Text('Devices signed in to this gateway'),
              onTap: () => Navigator.of(context).push<void>(
                MaterialPageRoute<void>(
                  builder: (_) => SessionsPage(
                    admin: admin,
                    onSignedOut: () {
                      Navigator.of(context).popUntil((route) => route.isFirst);
                      unawaited(widget.session.signOut());
                    },
                  ),
                ),
              ),
            ),
            ListTile(
              leading: const Icon(Icons.history),
              title: const Text('Activity'),
              subtitle: const Text('Sign-ins, messages sent, calls placed'),
              onTap: () => Navigator.of(context).push<void>(
                MaterialPageRoute<void>(
                  builder: (_) => AuditPage(admin: admin),
                ),
              ),
            ),
          ] else if (stored != null)
            const ListTile(
              leading: Icon(Icons.admin_panel_settings_outlined),
              title: Text('Sessions and two-factor sign-in'),
              subtitle: Text(
                'Managed from a device signed in with Admin access.',
              ),
            ),
          const Divider(),
          const SectionHeader(title: 'About'),
          const ListTile(
            title: Text('Rackphone client'),
            subtitle: Text(_version),
          ),
          ListTile(
            title: const Text('Gateway'),
            subtitle: Text(_health?.version ?? '—'),
          ),
        ],
      ),
    );
  }

  static String _quietHours(ServiceSettings settings) {
    final start = settings.quietStartMinute;
    final end = settings.quietEndMinute;
    if (start == null || end == null) return 'Off';
    return '${_clock(start)} – ${_clock(end)}';
  }

  static String _clock(int minute) {
    String two(int value) => value.toString().padLeft(2, '0');
    return '${two(minute ~/ 60)}:${two(minute % 60)}';
  }

  static TimeOfDay _time(int minute) =>
      TimeOfDay(hour: minute ~/ 60, minute: minute % 60);

  static String _dateTime(int seconds) {
    final value = DateTime.fromMillisecondsSinceEpoch(seconds * 1000);
    String two(int part) => part.toString().padLeft(2, '0');
    return '${value.year}-${two(value.month)}-${two(value.day)} '
        '${two(value.hour)}:${two(value.minute)}';
  }

  static ServiceSettings _copy(
    ServiceSettings settings, {
    bool? notifyOnSms,
    bool? notifyOnCalls,
    bool? notifyOnNotifications,
    int? quietStartMinute,
    int? quietEndMinute,
    bool clearQuiet = false,
  }) => ServiceSettings(
    notifyOnSms: notifyOnSms ?? settings.notifyOnSms,
    notifyOnCalls: notifyOnCalls ?? settings.notifyOnCalls,
    notifyOnNotifications:
        notifyOnNotifications ?? settings.notifyOnNotifications,
    quietStartMinute: clearQuiet
        ? null
        : quietStartMinute ?? settings.quietStartMinute,
    quietEndMinute: clearQuiet
        ? null
        : quietEndMinute ?? settings.quietEndMinute,
  );
}

/// Matches `version` in pubspec.yaml.
const _version = '0.1.0';
