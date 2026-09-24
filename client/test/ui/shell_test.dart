import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/session/session_controller.dart';
import 'package:rackphone_client/src/session/token_store.dart';
import 'package:rackphone_client/src/ui/shell.dart';
import 'package:rackphone_client/src/ui/theme.dart';

import '../support/fake_gateway.dart';

void main() {
  testWidgets('the top bar names the unit and its state', (tester) async {
    final session = await _signedIn(FakeGateway());
    await tester.pumpWidget(_shell(session));
    await tester.pump();

    expect(find.text('lisa01'), findsOneWidget);
    expect(find.text('Online · 72%'), findsOneWidget);
  });

  testWidgets('the drawer offers only what the unit may do', (tester) async {
    final session = await _signedIn(
      FakeGateway(
        units: [
          unit(capabilities: {'sms'}),
        ],
      ),
    );
    await tester.pumpWidget(_shell(session));
    await tester.pump();
    await _openDrawer(tester);

    expect(find.text('Messages'), findsWidgets);
    expect(find.text('Phone'), findsOneWidget);
    expect(find.text('Notifications'), findsNothing);
    expect(find.text('Screen'), findsNothing);
    expect(find.text('Files'), findsNothing);
    expect(find.text('Settings'), findsOneWidget);
  });

  testWidgets('the drawer counts what streamed in unseen', (tester) async {
    final gateway = FakeGateway();
    final session = await _signedIn(gateway);
    await tester.pumpWidget(_shell(session));
    await tester.pump();

    gateway.add(event(1, address: 'Bank'));
    gateway.add(event(2, kind: 'call', direction: 'missed'));
    await tester.pump();
    await _openDrawer(tester);

    final messages = find.ancestor(
      of: find.text('Messages').last,
      matching: find.byType(Row),
    );
    expect(
      find.descendant(of: messages.first, matching: find.text('1')),
      findsOneWidget,
    );
  });

  testWidgets('choosing a destination swaps the page and searching appears', (
    tester,
  ) async {
    final session = await _signedIn(
      FakeGateway(eventsValue: [event(1, address: 'Bank')]),
    );
    await tester.pumpWidget(_shell(session));
    await tester.pump();
    expect(find.byTooltip('Search'), findsNothing);

    await _openDrawer(tester);
    await tester.tap(find.text('Messages').last);
    await tester.pumpAndSettle();

    expect(find.text('See all'), findsNothing);
    expect(find.text('Bank'), findsOneWidget);
    expect(find.byTooltip('Search'), findsOneWidget);
    expect(find.byTooltip('New conversation'), findsOneWidget);
  });

  testWidgets('Phone offers the dial pad only where calls are possible', (
    tester,
  ) async {
    final placed = <String>[];
    final session = await _signedIn(
      FakeGateway(
        units: [
          unit(capabilities: {'sms', 'calls'}),
        ],
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        theme: rackphoneTheme(),
        home: AppShell(
          session: session,
          onCall: (unit, address) => placed.add('$unit $address'),
        ),
      ),
    );
    await tester.pump();
    await _openDrawer(tester);
    await tester.tap(find.text('Phone'));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Dial a number'));
    await tester.pumpAndSettle();
    expect(find.text('Call from lisa01'), findsOneWidget);
    for (final key in ['1', '2', '3']) {
      await tester.tap(find.text(key));
    }
    await tester.pump();
    expect(find.text('123'), findsOneWidget);
    await tester.tap(find.byTooltip('Call'));
    await tester.pumpAndSettle();
    expect(placed, ['lisa01 123']);
  });

  testWidgets('without the calls capability there is no dial pad', (
    tester,
  ) async {
    final session = await _signedIn(FakeGateway());
    await tester.pumpWidget(
      MaterialApp(
        theme: rackphoneTheme(),
        home: AppShell(session: session, onCall: (_, _) {}),
      ),
    );
    await tester.pump();
    await _openDrawer(tester);
    await tester.tap(find.text('Phone'));
    await tester.pumpAndSettle();

    expect(find.byTooltip('Dial a number'), findsNothing);
  });

  testWidgets('another unit replaces the inbox and ends the old stream', (
    tester,
  ) async {
    final gateway = FakeGateway(
      units: [
        unit(),
        unit(name: 'lisa02'),
      ],
    );
    final session = await _signedIn(gateway);
    await tester.pumpWidget(_shell(session));
    await tester.pump();
    expect(gateway.eventQueries.map((q) => q.unit).toSet(), {'lisa01'});

    await _openDrawer(tester);
    await tester.tap(find.byTooltip('Choose unit'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('lisa02').last);
    await tester.pumpAndSettle();

    expect(session.selectedUnit?.name, 'lisa02');
    expect(gateway.eventQueries.map((q) => q.unit).toSet(), {
      'lisa01',
      'lisa02',
    });
    expect(gateway.cancelledStreams, 1);
  });

  testWidgets('files open from the drawer when the unit allows them', (
    tester,
  ) async {
    final session = await _signedIn(
      FakeGateway(
        units: [
          unit(capabilities: {'sms', 'files'}),
        ],
      ),
    );
    await tester.pumpWidget(_shell(session));
    await tester.pump();

    await _openDrawer(tester);
    await tester.tap(find.text('Files'));
    await tester.pumpAndSettle();

    expect(find.text('Files · lisa01'), findsOneWidget);
    expect(find.text('payload.bin'), findsOneWidget);
  });
}

Future<void> _openDrawer(WidgetTester tester) async {
  await tester.tap(find.byTooltip('Open navigation menu'));
  await tester.pumpAndSettle();
}

Future<SessionController> _signedIn(FakeGateway gateway) async {
  final session = SessionController(
    tokenStore: InMemoryTokenStore(),
    gatewayFactory: (_) => gateway,
  );
  await session.signIn(
    baseUrl: Uri.parse('https://rack.example/'),
    username: 'admin',
    password: 'secret',
    deviceLabel: 'test',
  );
  return session;
}

Widget _shell(SessionController session) => MaterialApp(
  theme: rackphoneTheme(),
  home: AppShell(session: session),
);
