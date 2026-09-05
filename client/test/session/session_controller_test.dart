import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/errors.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/session/session_controller.dart';
import 'package:rackphone_client/src/session/token_store.dart';

void main() {
  test('restore without a stored session ends signed out', () async {
    final controller = SessionController(
      tokenStore: InMemoryTokenStore(),
      gatewayFactory: (_) => FakeGateway(),
    );

    await controller.restore();

    expect(controller.state.status, SessionStatus.signedOut);
  });

  test(
    'restore renews the session, writes rotation, and loads units',
    () async {
      final store = CountingStore(_stored(refreshToken: 'old-refresh'));
      final gateway = FakeGateway(
        refreshedTokens: _tokens(refreshToken: 'new-refresh'),
        unitResults: [_units('one', 'two')],
      );
      final controller = SessionController(
        tokenStore: store,
        gatewayFactory: (_) => gateway,
      );

      await controller.restore();

      expect(gateway.refreshTokens, ['old-refresh']);
      expect(controller.state.status, SessionStatus.signedIn);
      expect(controller.state.units.map((unit) => unit.name), ['one', 'two']);
      expect(store.value?.refreshToken, 'new-refresh');
      expect(store.writes, 1);
    },
  );

  test('refused renewal clears the unusable stored token', () async {
    final store = CountingStore(_stored());
    final controller = SessionController(
      tokenStore: store,
      gatewayFactory: (_) =>
          FakeGateway(refreshFailure: const GatewayAuthException('revoked')),
    );

    await controller.restore();

    expect(controller.state.status, SessionStatus.signedOut);
    expect(controller.state.failure, isA<GatewayAuthException>());
    expect(store.value, isNull);
    expect(store.clears, 1);
  });

  test('an unreachable gateway at startup keeps the session', () async {
    // A thirty-day credential must not be thrown away because the Wi-Fi was
    // down at launch: a password cannot fix a network.
    final store = CountingStore(_stored());
    final controller = SessionController(
      tokenStore: store,
      gatewayFactory: (_) => FakeGateway(
        refreshFailure: const GatewayNetworkException('no route to host'),
      ),
    );

    await controller.restore();

    expect(controller.state.status, SessionStatus.offline);
    expect(store.value, isNotNull);
    expect(store.clears, 0);
  });

  test('a failed unit reload leaves the units on screen', () async {
    final gateway = FakeGateway(unitResults: [_units('one', 'two')]);
    final store = CountingStore(_stored());
    final controller = SessionController(
      tokenStore: store,
      gatewayFactory: (_) => gateway,
    );
    await controller.restore();

    gateway.unitsFailure = const GatewayNetworkException('dropped');
    await controller.refreshUnits();

    expect(controller.state.status, SessionStatus.signedIn);
    expect(controller.state.units.map((unit) => unit.name), ['one', 'two']);
    expect(store.clears, 0);
  });

  test('a refused unit reload does end the session', () async {
    final gateway = FakeGateway(unitResults: [_units('one')]);
    final store = CountingStore(_stored());
    final controller = SessionController(
      tokenStore: store,
      gatewayFactory: (_) => gateway,
    );
    await controller.restore();

    gateway.unitsFailure = const GatewayAuthException('revoked');
    await controller.refreshUnits();

    expect(controller.state.status, SessionStatus.signedOut);
    expect(store.value, isNull);
  });

  test('sign in persists one record without the access token', () async {
    final store = CountingStore();
    final gateway = FakeGateway(
      loginTokens: _tokens(accessToken: 'memory-only'),
      unitResults: [_units('one')],
    );
    final controller = SessionController(
      tokenStore: store,
      gatewayFactory: (_) => gateway,
    );

    await controller.signIn(
      baseUrl: Uri.parse('https://rack.example/'),
      username: 'admin',
      password: 'secret',
      deviceLabel: 'phone',
    );

    expect(store.writes, 1);
    expect(store.value?.refreshToken, 'refresh');
    expect(store.value.toString(), isNot(contains('memory-only')));
    expect(controller.state.status, SessionStatus.signedIn);
  });

  test('wrong password becomes a renderable signed-out state', () async {
    final controller = SessionController(
      tokenStore: CountingStore(),
      gatewayFactory: (_) => FakeGateway(
        loginFailure: const GatewayAuthException('wrong password'),
      ),
    );

    await controller.signIn(
      baseUrl: Uri.parse('https://rack.example/'),
      username: 'admin',
      password: 'wrong',
      deviceLabel: 'phone',
    );

    expect(controller.state.status, SessionStatus.signedOut);
    expect(
      (controller.state.failure as GatewayAuthException).reason,
      'wrong password',
    );
  });

  test('locked account retains its retry duration in state', () async {
    const duration = Duration(hours: 6);
    final controller = SessionController(
      tokenStore: CountingStore(),
      gatewayFactory: (_) => FakeGateway(
        loginFailure: const GatewayLockedException(retryAfter: duration),
      ),
    );

    await controller.signIn(
      baseUrl: Uri.parse('https://rack.example/'),
      username: 'admin',
      password: 'wrong',
      deviceLabel: 'phone',
    );

    final failure = controller.state.failure as GatewayLockedException;
    expect(failure.retryAfter, duration);
  });

  test('sign out clears locally even when remote revocation fails', () async {
    final store = CountingStore();
    final gateway = FakeGateway(
      loginTokens: _tokens(),
      unitResults: [_units('one')],
      logoutFailure: const GatewayUnavailableException(),
    );
    final controller = SessionController(
      tokenStore: store,
      gatewayFactory: (_) => gateway,
    );
    await controller.signIn(
      baseUrl: Uri.parse('https://rack.example/'),
      username: 'admin',
      password: 'secret',
      deviceLabel: 'phone',
    );

    await controller.signOut();

    expect(store.value, isNull);
    expect(store.clears, 1);
    expect(controller.state.status, SessionStatus.signedOut);
  });

  test(
    'selection survives reload and falls back if its unit disappears',
    () async {
      final gateway = FakeGateway(
        loginTokens: _tokens(),
        unitResults: [
          _units('one', 'two'),
          _units('two', 'three'),
          _units('one', 'three'),
        ],
      );
      final controller = SessionController(
        tokenStore: CountingStore(),
        gatewayFactory: (_) => gateway,
      );
      await controller.signIn(
        baseUrl: Uri.parse('https://rack.example/'),
        username: 'admin',
        password: 'secret',
        deviceLabel: 'phone',
      );
      expect(controller.selectedUnit?.name, 'one');
      controller.select('two');

      await controller.refreshUnits();
      expect(controller.selectedUnit?.name, 'two');

      await controller.refreshUnits();
      expect(controller.selectedUnit?.name, 'one');
    },
  );
}

final class CountingStore implements TokenStore {
  CountingStore([this.value]);

  StoredSession? value;
  int writes = 0;
  int clears = 0;

  @override
  Future<StoredSession?> read() async => value;

  @override
  Future<void> write(StoredSession session) async {
    writes++;
    value = session;
  }

  @override
  Future<void> clear() async {
    clears++;
    value = null;
  }
}

final class FakeGateway implements GatewayApi {
  FakeGateway({
    this.loginTokens,
    this.refreshedTokens,
    this.loginFailure,
    this.refreshFailure,
    this.logoutFailure,
    this.unitsFailure,
    List<List<RackUnit>>? unitResults,
  }) : unitResults = unitResults ?? [_units()];

  final Tokens? loginTokens;
  final Tokens? refreshedTokens;
  final GatewayException? loginFailure;
  final GatewayException? refreshFailure;
  final GatewayException? logoutFailure;
  GatewayException? unitsFailure;
  final List<List<RackUnit>> unitResults;
  final List<String> refreshTokens = [];
  int _unitIndex = 0;

  @override
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) async {
    if (loginFailure case final failure?) throw failure;
    return loginTokens ?? _tokens(scope: scope);
  }

  @override
  Future<Tokens> refresh(String refreshToken) async {
    refreshTokens.add(refreshToken);
    if (refreshFailure case final failure?) throw failure;
    return refreshedTokens ?? _tokens();
  }

  @override
  Future<void> logOut(String refreshToken) async {
    if (logoutFailure case final failure?) throw failure;
  }

  @override
  Future<List<RackUnit>> units() async {
    if (unitsFailure case final failure?) throw failure;
    return unitResults[_unitIndex < unitResults.length
        ? _unitIndex++
        : _unitIndex - 1];
  }

  @override
  Future<List<GatewayEvent>> calls({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();

  @override
  void close() {}

  @override
  Future<List<GatewayEvent>> events({
    String? kind,
    String? unit,
    int? since,
    int? limit,
  }) => throw UnimplementedError();

  @override
  Future<GatewayHealth> health() => throw UnimplementedError();

  @override
  Future<List<GatewayEvent>> messages({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();

  @override
  Future<GatewayStats> stats() => throw UnimplementedError();

  @override
  Stream<GatewayEvent> stream() => throw UnimplementedError();
}

StoredSession _stored({String refreshToken = 'refresh'}) => StoredSession(
  baseUrl: Uri.parse('https://rack.example/'),
  refreshToken: refreshToken,
  deviceLabel: 'phone',
  scope: 'control',
  refreshExpiresAt: 1000,
);

Tokens _tokens({
  String refreshToken = 'refresh',
  String accessToken = 'access',
  String scope = 'control',
}) => Tokens(
  refreshToken: refreshToken,
  accessToken: accessToken,
  scope: scope,
  refreshExpiresAt: 2000,
  accessExpiresAt: 1500,
);

List<RackUnit> _units([String? first, String? second]) => [
  if (first != null)
    RackUnit(name: first, label: first, capabilities: const {'sms'}),
  if (second != null)
    RackUnit(name: second, label: second, capabilities: const {'sms'}),
];
