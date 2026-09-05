import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/session/session_controller.dart';
import 'package:rackphone_client/src/session/token_store.dart';
import 'package:rackphone_client/src/ui/home_page.dart';
import 'package:rackphone_client/src/ui/theme.dart';

void main() {
  testWidgets('unit bar renders every unit and marks the selected one', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1600, 900));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final controller = await _signedInController();
    controller.select('unit-4');
    await tester.pumpWidget(_home(controller));

    for (var index = 1; index <= 8; index++) {
      expect(find.text('unit-$index'), findsOneWidget);
    }
    final selected = tester.widget<Text>(find.text('unit-4'));
    final other = tester.widget<Text>(find.text('unit-3'));
    expect(selected.style?.fontWeight, FontWeight.w700);
    expect(other.style?.fontWeight, FontWeight.w500);
  });

  testWidgets('switching destination keeps the selected unit', (tester) async {
    final controller = await _signedInController();
    controller.select('unit-2');
    await tester.pumpWidget(_home(controller));

    await tester.tap(find.text('Messages'));
    await tester.pump();

    expect(controller.selectedUnit?.name, 'unit-2');
    expect(find.text('Messages'), findsNWidgets(2));
    final selected = tester.widget<Text>(find.text('unit-2'));
    expect(selected.style?.fontWeight, FontWeight.w700);
  });
}

Future<SessionController> _signedInController() async {
  final controller = SessionController(
    tokenStore: InMemoryTokenStore(),
    gatewayFactory: (_) => _HomeGateway(),
  );
  await controller.signIn(
    baseUrl: Uri.parse('https://rack.example/'),
    username: 'admin',
    password: 'secret',
    deviceLabel: 'test device',
  );
  return controller;
}

Widget _home(SessionController controller) => MaterialApp(
  theme: rackphoneTheme(),
  home: ListenableBuilder(
    listenable: controller,
    builder: (context, _) => HomePage(controller: controller),
  ),
);

final class _HomeGateway implements GatewayApi {
  @override
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) async => const Tokens(
    refreshToken: 'refresh',
    accessToken: 'access',
    scope: 'control',
    refreshExpiresAt: 1,
    accessExpiresAt: 1,
  );

  @override
  Future<List<RackUnit>> units() async => List<RackUnit>.generate(
    8,
    (index) => RackUnit(
      name: 'unit-${index + 1}',
      label: 'Unit ${index + 1}',
      capabilities: const {'sms'},
    ),
  );

  @override
  void close() {}

  @override
  Future<void> logOut(String refreshToken) async {}

  @override
  Future<Tokens> refresh(String refreshToken) => throw UnimplementedError();

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
