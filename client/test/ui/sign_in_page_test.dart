import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/errors.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/session/session_controller.dart';
import 'package:rackphone_client/src/session/token_store.dart';
import 'package:rackphone_client/src/ui/app.dart';

void main() {
  testWidgets('malformed address is refused without a gateway call', (
    tester,
  ) async {
    final gateway = _FakeGateway();
    await tester.pumpWidget(_app(gateway));
    await tester.pumpAndSettle();

    await tester.enterText(
      find.widgetWithText(TextFormField, 'Server address'),
      'rack.local',
    );
    await tester.tap(find.text('Sign in'));
    await tester.pump();

    expect(
      find.text('Enter an absolute http or https address.'),
      findsOneWidget,
    );
    expect(gateway.loginCalls, 0);
  });

  testWidgets('wrong password explains the refusal', (tester) async {
    final gateway = _FakeGateway(
      loginFailures: [const GatewayAuthException('invalid credentials')],
    );
    await tester.pumpWidget(_app(gateway));
    await tester.pumpAndSettle();
    await _completeRequiredFields(tester);

    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();

    expect(find.text('Wrong username or password.'), findsOneWidget);
  });

  testWidgets('locked account rounds its wait up to minutes', (tester) async {
    final gateway = _FakeGateway(
      loginFailures: [
        const GatewayLockedException(retryAfter: Duration(seconds: 61)),
      ],
    );
    await tester.pumpWidget(_app(gateway));
    await tester.pumpAndSettle();
    await _completeRequiredFields(tester);

    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();

    expect(
      find.text('Too many attempts. Try again in 2 minutes.'),
      findsOneWidget,
    );
  });

  testWidgets('authenticator field appears only after the gateway asks', (
    tester,
  ) async {
    final gateway = _FakeGateway(
      loginFailures: [const GatewayForbiddenException('totp_required')],
    );
    await tester.pumpWidget(_app(gateway));
    await tester.pumpAndSettle();
    expect(find.text('Authenticator code'), findsNothing);
    await _completeRequiredFields(tester);

    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();

    expect(find.text('Authenticator code'), findsOneWidget);
    expect(
      find.text('Enter the code from your authenticator.'),
      findsOneWidget,
    );
  });

  testWidgets('offline page retries the stored gateway', (tester) async {
    final gateway = _FakeGateway(
      refreshFailure: const GatewayNetworkException('offline'),
    );
    final store = InMemoryTokenStore(
      StoredSession(
        baseUrl: Uri.parse('https://rack.example/'),
        refreshToken: 'refresh',
        deviceLabel: 'test device',
        scope: 'control',
        refreshExpiresAt: 1,
      ),
    );
    final controller = SessionController(
      tokenStore: store,
      gatewayFactory: (_) => gateway,
    );
    await tester.pumpWidget(RackphoneApp(sessionController: controller));
    await tester.pumpAndSettle();

    expect(
      find.text('The gateway at https://rack.example/ could not be reached.'),
      findsOneWidget,
    );
    expect(gateway.refreshCalls, 1);
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(gateway.refreshCalls, 2);
  });
}

RackphoneApp _app(_FakeGateway gateway) => RackphoneApp(
  sessionController: SessionController(
    tokenStore: InMemoryTokenStore(),
    gatewayFactory: (_) => gateway,
  ),
);

Future<void> _completeRequiredFields(WidgetTester tester) async {
  await tester.enterText(
    find.widgetWithText(TextFormField, 'Server address'),
    'https://rack.example/',
  );
  await tester.enterText(
    find.widgetWithText(TextFormField, 'Username'),
    'admin',
  );
  await tester.enterText(
    find.widgetWithText(TextFormField, 'Password'),
    'wrong',
  );
}

final class _FakeGateway implements GatewayApi {
  _FakeGateway({this.loginFailures = const [], this.refreshFailure});

  final List<GatewayException> loginFailures;
  final GatewayException? refreshFailure;
  int loginCalls = 0;
  int refreshCalls = 0;

  @override
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) async {
    final call = loginCalls++;
    if (call < loginFailures.length) throw loginFailures[call];
    return _tokens;
  }

  @override
  Future<Tokens> refresh(String refreshToken) async {
    refreshCalls++;
    if (refreshFailure case final failure?) throw failure;
    return _tokens;
  }

  @override
  Future<List<RackUnit>> units() async => const [];

  @override
  Future<void> logOut(String refreshToken) async {}

  @override
  void close() {}

  @override
  Future<List<GatewayEvent>> calls({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();

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

const _tokens = Tokens(
  refreshToken: 'refresh',
  accessToken: 'access',
  scope: 'control',
  refreshExpiresAt: 1,
  accessExpiresAt: 1,
);
