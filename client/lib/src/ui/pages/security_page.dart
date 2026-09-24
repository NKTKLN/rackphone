import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../api/errors.dart';
import '../../api/gateway_client.dart';
import '../../api/models.dart';
import '../widgets.dart';

/// Every device signed in to the gateway, each one revocable.
class SessionsPage extends StatefulWidget {
  const SessionsPage({
    required this.admin,
    required this.onSignedOut,
    super.key,
  });

  final GatewayAdminApi admin;

  /// Called after "sign out everywhere", which includes this device.
  final VoidCallback onSignedOut;

  @override
  State<SessionsPage> createState() => _SessionsPageState();
}

class _SessionsPageState extends State<SessionsPage> {
  List<Session>? _sessions;
  Object? _failure;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final sessions = await widget.admin.sessions();
      final now = DateTime.now().millisecondsSinceEpoch ~/ 1000;
      // Revoked and expired sessions are history, not devices to worry about.
      final live =
          sessions
              .where((s) => s.revokedAt == null && s.expiresAt > now)
              .toList()
            ..sort((a, b) => b.lastSeen.compareTo(a.lastSeen));
      if (!mounted) return;
      setState(() {
        _sessions = live;
        _failure = null;
      });
    } catch (failure) {
      if (mounted) setState(() => _failure = failure);
    }
  }

  Future<void> _revoke(Session session) async {
    final confirmed = await _confirm(
      context,
      title: 'Sign out ${session.deviceLabel}?',
      body: 'That device will have to sign in again.',
      action: 'Sign out',
    );
    if (!confirmed) return;
    await _guard(() => widget.admin.revokeSession(session.id));
    await _load();
  }

  Future<void> _revokeAll() async {
    final confirmed = await _confirm(
      context,
      title: 'Sign out everywhere?',
      body:
          'Every device is signed out, this one included. Use this when a '
          'phone is lost.',
      action: 'Sign out everywhere',
    );
    if (!confirmed) return;
    if (await _guard(widget.admin.revokeAllSessions)) widget.onSignedOut();
  }

  Future<bool> _guard(Future<void> Function() action) async {
    try {
      await action();
      return true;
    } catch (failure) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(adminFailureText(failure))));
      }
      return false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final sessions = _sessions;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Sessions'),
        actions: <Widget>[
          TextButton(
            onPressed: sessions == null ? null : _revokeAll,
            child: const Text('Sign out all'),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: sessions == null
            ? EmptyState(
                text: _failure == null
                    ? 'Loading…'
                    : adminFailureText(_failure!),
              )
            : ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: <Widget>[
                  for (final session in sessions)
                    ListTile(
                      leading: const Icon(Icons.devices_outlined),
                      title: Text(session.deviceLabel),
                      subtitle: Text(
                        '${session.scope} access · last seen '
                        '${_dateTime(session.lastSeen)}',
                      ),
                      trailing: IconButton(
                        tooltip: 'Sign out ${session.deviceLabel}',
                        icon: const Icon(Icons.logout),
                        onPressed: () => _revoke(session),
                      ),
                    ),
                ],
              ),
      ),
    );
  }
}

/// The gateway's action log, newest first.
class AuditPage extends StatefulWidget {
  const AuditPage({required this.admin, super.key});

  final GatewayAdminApi admin;

  @override
  State<AuditPage> createState() => _AuditPageState();
}

class _AuditPageState extends State<AuditPage> {
  List<AuditEntry>? _entries;
  Object? _failure;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final entries = await widget.admin.audit(limit: 200);
      if (!mounted) return;
      setState(() {
        _entries = entries;
        _failure = null;
      });
    } catch (failure) {
      if (mounted) setState(() => _failure = failure);
    }
  }

  @override
  Widget build(BuildContext context) {
    final entries = _entries;
    return Scaffold(
      appBar: AppBar(title: const Text('Activity')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: entries == null || entries.isEmpty
            ? EmptyState(
                text: _failure != null
                    ? adminFailureText(_failure!)
                    : entries == null
                    ? 'Loading…'
                    : 'Nothing has been recorded yet.',
              )
            : ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                itemCount: entries.length,
                itemBuilder: (context, index) {
                  final entry = entries[index];
                  final what = <String>[
                    ?entry.subject,
                    ?entry.detail,
                  ].where((part) => part.isNotEmpty).join(' · ');
                  return ListTile(
                    title: Text(_actionName(entry.action)),
                    subtitle: Text(
                      [
                        if (what.isNotEmpty) what,
                        _dateTime(entry.at),
                      ].join('\n'),
                    ),
                    isThreeLine: what.isNotEmpty,
                  );
                },
              ),
      ),
    );
  }

  /// `send_sms` reads as "Send sms"; that is plain enough for a log.
  static String _actionName(String action) {
    final words = action.replaceAll('_', ' ').trim();
    if (words.isEmpty) return 'Action';
    return words[0].toUpperCase() + words.substring(1);
  }
}

/// Turns two-factor sign-in on, showing the secret and recovery codes once.
Future<bool> enableTotp(BuildContext context, GatewayAdminApi admin) async {
  final password = await _askPassword(context, 'Turn on two-factor sign-in');
  if (password == null || !context.mounted) return false;
  final TotpEnrollment enrollment;
  try {
    enrollment = await admin.enableTotp(password);
  } catch (failure) {
    if (context.mounted) _say(context, adminFailureText(failure));
    return false;
  }
  if (!context.mounted) return true;
  await showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (context) => AlertDialog(
      title: const Text('Add this to your authenticator'),
      content: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const Text('Secret key'),
            SelectableText(
              enrollment.secret,
              style: const TextStyle(fontFamily: 'monospace'),
            ),
            const SizedBox(height: 16),
            const Text(
              'Recovery codes. Each works once, if the authenticator is lost. '
              'They are not shown again.',
            ),
            const SizedBox(height: 8),
            SelectableText(
              enrollment.recoveryCodes.join('\n'),
              style: const TextStyle(fontFamily: 'monospace'),
            ),
          ],
        ),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Clipboard.setData(
            ClipboardData(
              text:
                  '${enrollment.secret}\n\n'
                  '${enrollment.recoveryCodes.join('\n')}',
            ),
          ),
          child: const Text('Copy'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Done'),
        ),
      ],
    ),
  );
  return true;
}

/// Turns two-factor sign-in off, after the password.
Future<bool> disableTotp(BuildContext context, GatewayAdminApi admin) async {
  final password = await _askPassword(context, 'Turn off two-factor sign-in');
  if (password == null || !context.mounted) return false;
  try {
    await admin.disableTotp(password);
    return true;
  } catch (failure) {
    if (context.mounted) _say(context, adminFailureText(failure));
    return false;
  }
}

/// Says why an administrative action failed.
String adminFailureText(Object failure) => switch (failure) {
  GatewayForbiddenException() =>
    'This needs admin access. Sign in again with Admin access on.',
  GatewayAuthException() => 'Wrong password.',
  GatewayNetworkException() => 'The gateway could not be reached.',
  _ => 'That did not work: $failure',
};

Future<String?> _askPassword(BuildContext context, String title) =>
    showDialog<String>(
      context: context,
      builder: (context) => _PasswordDialog(title: title),
    );

/// Owns its field, so the text survives until the dialog has finished closing.
class _PasswordDialog extends StatefulWidget {
  const _PasswordDialog({required this.title});

  final String title;

  @override
  State<_PasswordDialog> createState() => _PasswordDialogState();
}

class _PasswordDialogState extends State<_PasswordDialog> {
  final _password = TextEditingController();

  @override
  void dispose() {
    _password.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.title),
    content: TextField(
      controller: _password,
      autofocus: true,
      obscureText: true,
      decoration: const InputDecoration(labelText: 'Password'),
      onSubmitted: (value) => Navigator.pop(context, value),
    ),
    actions: <Widget>[
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () => Navigator.pop(context, _password.text),
        child: const Text('Continue'),
      ),
    ],
  );
}

Future<bool> _confirm(
  BuildContext context, {
  required String title,
  required String body,
  required String action,
}) async =>
    await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(action),
          ),
        ],
      ),
    ) ??
    false;

void _say(BuildContext context, String text) =>
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

String _dateTime(int seconds) {
  final value = DateTime.fromMillisecondsSinceEpoch(seconds * 1000);
  String two(int part) => part.toString().padLeft(2, '0');
  return '${value.year}-${two(value.month)}-${two(value.day)} '
      '${two(value.hour)}:${two(value.minute)}';
}
