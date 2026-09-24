import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/errors.dart';
import '../api/gateway_client.dart';
import '../api/models.dart';
import 'token_store.dart';

enum SessionStatus { unknown, signedOut, signingIn, signedIn, offline }

/// One complete, immutable answer to whether the application is signed in.
final class SessionState {
  const SessionState._({
    required this.status,
    this.failure,
    this.scope,
    this.units = const [],
  });

  const SessionState.unknown() : this._(status: SessionStatus.unknown);

  const SessionState.signedOut([GatewayException? failure])
    : this._(status: SessionStatus.signedOut, failure: failure);

  const SessionState.signingIn() : this._(status: SessionStatus.signingIn);

  /// The credential is intact and the gateway is simply out of reach.
  ///
  /// Distinct from [SessionState.signedOut] because the two need opposite
  /// screens: one asks for a password that is not the problem, the other
  /// offers to try again.
  const SessionState.offline(GatewayException failure)
    : this._(status: SessionStatus.offline, failure: failure);

  SessionState.signedIn({required String scope, required List<RackUnit> units})
    : this._(
        status: SessionStatus.signedIn,
        scope: scope,
        units: List.unmodifiable(units),
      );

  final SessionStatus status;
  final GatewayException? failure;
  final String? scope;
  final List<RackUnit> units;
}

/// Owns the boundary between durable identity and the gateway's in-memory
/// access token, leaving screens to observe a single state instead.
final class SessionController extends ChangeNotifier {
  SessionController({required this.tokenStore, this.gatewayFactory});

  final TokenStore tokenStore;
  final GatewayApi Function(Uri baseUrl)? gatewayFactory;
  GatewayApi? _gateway;
  StoredSession? _session;
  SessionState _state = const SessionState.unknown();
  String? _selectedName;

  SessionState get state => _state;

  /// Screens receive only the authenticated API boundary, never its durable
  /// refresh token or the store that protects it.
  GatewayApi? get gateway => _gateway;

  RackUnit? get selectedUnit {
    final units = _state.units;
    if (units.isEmpty) return null;
    final selectedName = _selectedName;
    if (selectedName != null) {
      for (final unit in units) {
        if (unit.name == selectedName) return unit;
      }
    }
    return units.first;
  }

  Future<void> restore() async {
    final session = await tokenStore.read();
    if (session == null) {
      _setState(const SessionState.signedOut());
      return;
    }
    _session = session;
    final gateway = _newGateway(session.baseUrl);
    _replaceGateway(gateway);
    try {
      final tokens = await gateway.refresh(session.refreshToken);
      await _persistTokens(tokens);
      await _loadUnits(gateway, tokens.scope);
    } on GatewayAuthException catch (failure) {
      await tokenStore.clear();
      _session = null;
      _setState(SessionState.signedOut(failure));
    } on GatewayException catch (failure) {
      // The refresh token is still good; the network is not. Throwing it away
      // here would demand a password for a problem a password cannot fix.
      _setState(SessionState.offline(failure));
    }
  }

  Future<void> signIn({
    required Uri baseUrl,
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) async {
    _setState(const SessionState.signingIn());
    final gateway = _newGateway(baseUrl);
    _replaceGateway(gateway);
    _session = StoredSession(
      baseUrl: baseUrl,
      refreshToken: '',
      deviceLabel: deviceLabel,
      scope: scope,
      refreshExpiresAt: 0,
    );
    try {
      final tokens = await gateway.logIn(
        username: username,
        password: password,
        deviceLabel: deviceLabel,
        totpCode: totpCode,
        recoveryCode: recoveryCode,
        scope: scope,
      );
      await _persistTokens(tokens);
      await _loadUnits(gateway, tokens.scope);
    } on GatewayException catch (failure) {
      _session = null;
      _setState(SessionState.signedOut(failure));
    }
  }

  Future<void> signOut() async {
    final gateway = _gateway;
    final refreshToken = _session?.refreshToken;
    try {
      if (gateway != null && refreshToken != null && refreshToken.isNotEmpty) {
        await gateway.logOut(refreshToken);
      }
    } catch (_) {
      // Revoking remotely is best effort; removing the local credential is the
      // operation that guarantees this device is signed out.
    } finally {
      await tokenStore.clear();
      _session = null;
      _selectedName = null;
      _setState(const SessionState.signedOut());
    }
  }

  Future<void> refreshUnits() async {
    final gateway = _gateway;
    final scope = _state.scope ?? _session?.scope;
    if (gateway == null || scope == null) return;
    try {
      await _loadUnits(gateway, scope);
    } on GatewayAuthException catch (failure) {
      // Only a refused credential ends the session. A reload that failed for
      // any other reason leaves the units already on screen alone, because
      // emptying them would be a worse answer than showing them slightly stale.
      await tokenStore.clear();
      _session = null;
      _setState(SessionState.signedOut(failure));
    } on GatewayException {
      // Reported by whoever asked for the reload, not by ending the session.
    }
  }

  void select(String name) {
    if (!_state.units.any((unit) => unit.name == name)) return;
    if (_selectedName == name) return;
    _selectedName = name;
    notifyListeners();
  }

  GatewayApi _newGateway(Uri baseUrl) {
    final factory = gatewayFactory;
    if (factory != null) return factory(baseUrl);
    return GatewayClient(
      baseUrl: baseUrl,
      refreshTokenProvider: () async => (await tokenStore.read())?.refreshToken,
      onTokensRenewed: _persistTokens,
    );
  }

  void _replaceGateway(GatewayApi gateway) {
    if (!identical(_gateway, gateway)) _gateway?.close();
    _gateway = gateway;
  }

  Future<void> _persistTokens(Tokens tokens) async {
    final current = _session;
    if (current == null) return;
    final renewed = StoredSession(
      baseUrl: current.baseUrl,
      refreshToken: tokens.refreshToken,
      deviceLabel: current.deviceLabel,
      scope: tokens.scope,
      refreshExpiresAt: tokens.refreshExpiresAt,
    );
    await tokenStore.write(renewed);
    _session = renewed;
  }

  Future<void> _loadUnits(GatewayApi gateway, String scope) async {
    final units = await gateway.units();
    final selectedName = _selectedName;
    if (selectedName == null ||
        !units.any((unit) => unit.name == selectedName)) {
      _selectedName = units.firstOrNull?.name;
    }
    _setState(SessionState.signedIn(scope: scope, units: units));
  }

  void _setState(SessionState state) {
    _state = state;
    notifyListeners();
  }

  @override
  void dispose() {
    _gateway?.close();
    super.dispose();
  }
}
