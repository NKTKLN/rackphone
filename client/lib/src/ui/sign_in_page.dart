import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../api/errors.dart';
import '../session/session_controller.dart';

/// Collects credentials while authentication state remains in the controller.
class SignInPage extends StatefulWidget {
  const SignInPage({
    required this.controller,
    this.failure,
    this.busy = false,
    super.key,
  });

  final SessionController controller;
  final GatewayException? failure;
  final bool busy;

  @override
  State<SignInPage> createState() => _SignInPageState();
}

class _SignInPageState extends State<SignInPage> {
  final _formKey = GlobalKey<FormState>();
  final _address = TextEditingController();
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _totp = TextEditingController();
  late final TextEditingController _deviceLabel;
  bool _totpRequested = false;

  @override
  void initState() {
    super.initState();
    _deviceLabel = TextEditingController(text: _defaultDeviceLabel());
    _rememberTotpRequest();
  }

  @override
  void didUpdateWidget(SignInPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    _rememberTotpRequest();
  }

  void _rememberTotpRequest() {
    final failure = widget.failure;
    if (failure is GatewayForbiddenException &&
        (failure.reason == 'totp_required' || failure.reason == 'bad_totp')) {
      _totpRequested = true;
    }
  }

  @override
  void dispose() {
    _address.dispose();
    _username.dispose();
    _password.dispose();
    _totp.dispose();
    _deviceLabel.dispose();
    super.dispose();
  }

  String _defaultDeviceLabel() => switch (defaultTargetPlatform) {
    TargetPlatform.android => 'Android device',
    TargetPlatform.iOS => 'iOS device',
    TargetPlatform.macOS => 'Mac',
    TargetPlatform.windows => 'Windows device',
    TargetPlatform.linux => 'Linux device',
    TargetPlatform.fuchsia => 'Fuchsia device',
  };

  String? _validateAddress(String? value) {
    final uri = Uri.tryParse(value?.trim() ?? '');
    if (uri == null ||
        !uri.isAbsolute ||
        (uri.scheme != 'http' && uri.scheme != 'https') ||
        uri.host.isEmpty) {
      return 'Enter an absolute http or https address.';
    }
    return null;
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    await widget.controller.signIn(
      baseUrl: Uri.parse(_address.text.trim()),
      username: _username.text.trim(),
      password: _password.text,
      deviceLabel: _deviceLabel.text.trim(),
      totpCode: _totpRequested ? _totp.text.trim() : null,
    );
  }

  String? _failureMessage() {
    final failure = widget.failure;
    return switch (failure) {
      GatewayAuthException() => 'Wrong username or password.',
      GatewayForbiddenException(reason: 'totp_required') =>
        'Enter the code from your authenticator.',
      GatewayForbiddenException(reason: 'bad_totp') =>
        'That code did not match. Codes change every 30 seconds.',
      GatewayLockedException(:final retryAfter) => _lockedMessage(retryAfter),
      GatewayUnavailableException() =>
        'This gateway has no administrator yet. Run '
            '`rackphone admin init` on the host.',
      GatewayNetworkException() =>
        'Could not reach ${_address.text.trim().isEmpty ? 'the gateway' : _address.text.trim()}.',
      GatewayProtocolException() =>
        'The gateway returned an invalid response. Check the address and try again.',
      null => null,
      _ => 'Sign-in failed. Check the address and try again.',
    };
  }

  String _lockedMessage(Duration duration) {
    if (duration < const Duration(minutes: 1)) {
      final seconds = duration.inSeconds.clamp(1, 59);
      return 'Too many attempts. Try again in $seconds '
          '${seconds == 1 ? 'second' : 'seconds'}.';
    }
    final minutes = (duration.inSeconds / Duration.secondsPerMinute).ceil();
    return 'Too many attempts. Try again in $minutes '
        '${minutes == 1 ? 'minute' : 'minutes'}.';
  }

  @override
  Widget build(BuildContext context) {
    final failureMessage = _failureMessage();
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    Text(
                      'Rackphone',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 24),
                    TextFormField(
                      controller: _address,
                      enabled: !widget.busy,
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        labelText: 'Server address',
                      ),
                      validator: _validateAddress,
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _username,
                      enabled: !widget.busy,
                      autocorrect: false,
                      decoration: const InputDecoration(labelText: 'Username'),
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _password,
                      enabled: !widget.busy,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: 'Password'),
                    ),
                    if (_totpRequested) ...<Widget>[
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: _totp,
                        enabled: !widget.busy,
                        keyboardType: TextInputType.number,
                        decoration: const InputDecoration(
                          labelText: 'Authenticator code',
                        ),
                      ),
                    ],
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _deviceLabel,
                      enabled: !widget.busy,
                      decoration: const InputDecoration(
                        labelText: 'Device label',
                      ),
                    ),
                    if (failureMessage != null) ...<Widget>[
                      const SizedBox(height: 16),
                      Text(
                        failureMessage,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ],
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: widget.busy ? null : _submit,
                      child: widget.busy
                          ? const Row(
                              mainAxisSize: MainAxisSize.min,
                              children: <Widget>[
                                SizedBox.square(
                                  dimension: 20,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                ),
                                SizedBox(width: 10),
                                Text('Sign in'),
                              ],
                            )
                          : const Text('Sign in'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
