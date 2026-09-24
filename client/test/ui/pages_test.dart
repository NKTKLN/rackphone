import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/errors.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/data/contact_book.dart';
import 'package:rackphone_client/src/data/files_controller.dart';
import 'package:rackphone_client/src/data/inbox_controller.dart';
import 'package:rackphone_client/src/data/unit_status_controller.dart';
import 'package:rackphone_client/src/screen/decoder.dart';
import 'package:rackphone_client/src/screen/protocol.dart';
import 'package:rackphone_client/src/screen/screen_controller.dart';
import 'package:rackphone_client/src/screen/screen_socket.dart';
import 'package:rackphone_client/src/service/service_settings.dart';
import 'package:rackphone_client/src/session/session_controller.dart';
import 'package:rackphone_client/src/session/token_store.dart';
import 'package:rackphone_client/src/ui/pages/dialpad_page.dart';
import 'package:rackphone_client/src/ui/pages/files_page.dart';
import 'package:rackphone_client/src/ui/pages/home_page.dart';
import 'package:rackphone_client/src/ui/pages/messages_page.dart';
import 'package:rackphone_client/src/ui/pages/phone_page.dart';
import 'package:rackphone_client/src/ui/pages/screen_page.dart';
import 'package:rackphone_client/src/ui/pages/settings_page.dart';
import 'package:rackphone_client/src/ui/theme.dart';

import '../support/fake_gateway.dart';

void main() {
  group('home', () {
    testWidgets('shows live values and the newest messages and calls', (
      tester,
    ) async {
      final gateway = FakeGateway(
        eventsValue: [
          event(1, address: 'Bank'),
          event(2, address: 'Andrew'),
          event(3, address: 'Olga'),
          event(4, kind: 'call', address: 'Mom', direction: 'missed'),
        ],
      );
      final (:status, :inbox) = await _loaded(gateway);
      await tester.pumpWidget(
        _app(
          HomePage(
            status: status,
            inbox: inbox,
            onOpen: (_) {},
            onOpenThread: (_) {},
          ),
        ),
      );

      expect(find.text('72%'), findsOneWidget);
      expect(find.text('32°C'), findsOneWidget);
      expect(find.text('3 d'), findsOneWidget);
      // Only the two newest conversations make it to Home.
      expect(find.text('Olga'), findsOneWidget);
      expect(find.text('Andrew'), findsOneWidget);
      expect(find.text('Bank'), findsNothing);
      expect(find.text('Mom'), findsOneWidget);
      expect(find.text('Missed'), findsOneWidget);
    });

    testWidgets('a refused telemetry call leaves the rest of Home up', (
      tester,
    ) async {
      final gateway = FakeGateway(eventsValue: [event(1, address: 'Bank')])
        ..telemetryFailure = const GatewayForbiddenException('denied');
      final (:status, :inbox) = await _loaded(gateway);
      await tester.pumpWidget(
        _app(
          HomePage(
            status: status,
            inbox: inbox,
            onOpen: (_) {},
            onOpenThread: (_) {},
          ),
        ),
      );

      expect(status.summary, 'Unreachable');
      expect(find.text('—'), findsNWidgets(3));
      expect(find.text('Bank'), findsOneWidget);
    });

    testWidgets('See all opens the matching destination', (tester) async {
      final (:status, :inbox) = await _loaded(FakeGateway());
      final opened = <InboxKind>[];
      await tester.pumpWidget(
        _app(
          HomePage(
            status: status,
            inbox: inbox,
            onOpen: opened.add,
            onOpenThread: (_) {},
          ),
        ),
      );

      await tester.tap(find.text('See all').last);
      expect(opened, [InboxKind.calls]);
    });
  });

  group('messages', () {
    testWidgets('lists one row per conversation and opens it', (tester) async {
      final gateway = FakeGateway(
        eventsValue: [
          event(1, address: 'Bank', body: 'first'),
          event(2, address: 'Andrew', body: 'hello'),
          event(3, address: 'Bank', body: 'code 4821'),
        ],
      );
      final (status: _, :inbox) = await _loaded(gateway);
      MessageThread? opened;
      await tester.pumpWidget(
        _app(MessagesPage(inbox: inbox, onOpenThread: (t) => opened = t)),
      );

      expect(find.text('Bank'), findsOneWidget);
      expect(find.text('code 4821'), findsOneWidget);
      expect(find.text('first'), findsNothing);
      expect(
        tester.getTopLeft(find.text('Bank')).dy,
        lessThan(tester.getTopLeft(find.text('Andrew')).dy),
      );

      await tester.tap(find.text('Bank'));
      expect(opened?.address, 'Bank');
    });

    testWidgets('an opened conversation shows every message and is read', (
      tester,
    ) async {
      final gateway = FakeGateway(eventsValue: [event(1, address: 'Bank')]);
      final (status: _, :inbox) = await _loaded(gateway);
      inbox.listen();
      gateway.add(event(2, address: 'Bank', body: 'fresh'));
      await tester.pump();
      expect(inbox.unseen(InboxKind.messages), 1);

      await tester.pumpWidget(
        MaterialApp(
          home: ThreadPage(inbox: inbox, address: 'Bank'),
        ),
      );
      await tester.pump();

      expect(find.text('body 1'), findsOneWidget);
      expect(find.text('fresh'), findsOneWidget);
      expect(inbox.unseen(InboxKind.messages), 0);
    });

    testWidgets('a reply goes out from the conversation and appears in it', (
      tester,
    ) async {
      final gateway = FakeGateway(eventsValue: [event(1, address: '+7900')]);
      final (status: _, :inbox) = await _loaded(gateway);
      await tester.pumpWidget(
        MaterialApp(
          home: ThreadPage(inbox: inbox, address: '+7900'),
        ),
      );

      await tester.enterText(find.byType(TextField), 'on my way');
      await tester.pump();
      await tester.tap(find.byTooltip('Send'));
      await tester.pump();

      expect(gateway.sent.single.body, 'on my way');
      expect(find.text('on my way'), findsOneWidget);
      final field = tester.widget<TextField>(find.byType(TextField));
      expect(field.controller!.text, isEmpty);
    });

    testWidgets('a failed send keeps the text and says why', (tester) async {
      final gateway = FakeGateway(eventsValue: [event(1, address: '+7900')])
        ..sendFailure = const GatewayProtocolException('HTTP 502');
      final (status: _, :inbox) = await _loaded(gateway);
      await tester.pumpWidget(
        MaterialApp(
          home: ThreadPage(inbox: inbox, address: '+7900'),
        ),
      );

      await tester.enterText(find.byType(TextField), 'on my way');
      await tester.pump();
      await tester.tap(find.byTooltip('Send'));
      await tester.pump();

      expect(find.text('The unit could not send the message.'), findsOneWidget);
      final field = tester.widget<TextField>(find.byType(TextField));
      expect(field.controller!.text, 'on my way');
    });

    testWidgets('a new conversation sends and continues in its thread', (
      tester,
    ) async {
      final gateway = FakeGateway();
      final (status: _, :inbox) = await _loaded(gateway);
      await tester.pumpWidget(MaterialApp(home: NewMessagePage(inbox: inbox)));

      await tester.enterText(find.byType(TextField).first, '+7 900 123-45');
      await tester.enterText(find.byType(TextField).last, 'hello');
      await tester.pump();
      await tester.tap(find.byTooltip('Send'));
      await tester.pumpAndSettle();

      expect(gateway.sent.single.to, '+790012345');
      expect(find.text('+790012345'), findsOneWidget);
      expect(find.text('hello'), findsOneWidget);
      expect(find.text('New conversation'), findsNothing);
    });

    testWidgets('a new conversation refuses what is not a number', (
      tester,
    ) async {
      final gateway = FakeGateway();
      final (status: _, :inbox) = await _loaded(gateway);
      await tester.pumpWidget(MaterialApp(home: NewMessagePage(inbox: inbox)));

      await tester.enterText(find.byType(TextField).first, 'mom');
      await tester.enterText(find.byType(TextField).last, 'hello');
      await tester.pump();
      await tester.tap(find.byTooltip('Send'));
      await tester.pump();

      expect(gateway.sent, isEmpty);
      expect(find.textContaining('Enter a number'), findsOneWidget);
    });

    testWidgets('says so when there is nothing to show', (tester) async {
      final (status: _, :inbox) = await _loaded(FakeGateway());
      await tester.pumpWidget(
        _app(MessagesPage(inbox: inbox, onOpenThread: (_) {})),
      );

      expect(
        find.text('Text messages to lisa01 will appear here.'),
        findsOneWidget,
      );
    });
  });

  group('contacts', () {
    testWidgets('names replace numbers in conversations and calls', (
      tester,
    ) async {
      final gateway =
          FakeGateway(
              eventsValue: [
                event(1, address: '+79001234567', body: 'hello'),
                event(2, kind: 'call', address: '89001234567'),
              ],
            )
            ..contactsValue = const [
              Contact(name: 'Andrew', number: '+79001234567'),
            ];
      final (:status, :inbox) = await _loaded(gateway);
      final contacts = ContactBook(gateway: gateway, unit: 'lisa01');
      await contacts.load();
      await tester.pumpWidget(
        _app(
          HomePage(
            status: status,
            inbox: inbox,
            contacts: contacts,
            onOpen: (_) {},
            onOpenThread: (_) {},
          ),
        ),
      );

      expect(find.text('Andrew'), findsNWidgets(2));
      expect(find.text('+79001234567'), findsNothing);
    });

    testWidgets('the address book lists letters with an index beside it', (
      tester,
    ) async {
      final gateway = FakeGateway()
        ..contactsValue = const [
          Contact(name: 'Andrew', number: '+7900'),
          Contact(name: 'Anna', number: '+7901'),
          Contact(name: 'Olga', number: '+7902'),
        ];
      final (status: _, :inbox) = await _loaded(gateway);
      final contacts = ContactBook(gateway: gateway, unit: 'lisa01');
      await contacts.load();
      final messaged = <String>[];
      await tester.pumpWidget(
        _app(
          PhonePage(inbox: inbox, contacts: contacts, onMessage: messaged.add),
        ),
      );

      await tester.tap(find.text('Contacts'));
      await tester.pumpAndSettle();
      expect(find.text('Andrew'), findsOneWidget);
      Finder jump(String letter) => find.byWidgetPredicate(
        (widget) =>
            widget is Text && widget.semanticsLabel == 'Jump to $letter',
      );
      expect(jump('A'), findsOneWidget);
      expect(jump('O'), findsOneWidget);

      await tester.tap(find.text('Olga'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Send message'));
      await tester.pumpAndSettle();
      expect(messaged, ['+7902']);
    });

    testWidgets('an unreadable address book says what to fix', (tester) async {
      final gateway = FakeGateway()..contactsFailure = StateError('denied');
      final (status: _, :inbox) = await _loaded(gateway);
      final contacts = ContactBook(gateway: gateway, unit: 'lisa01');
      await contacts.load();
      await tester.pumpWidget(
        _app(PhonePage(inbox: inbox, contacts: contacts)),
      );

      await tester.tap(find.text('Contacts'));
      await tester.pumpAndSettle();
      expect(find.textContaining('contacts permission'), findsOneWidget);
    });
  });

  testWidgets('phone lists calls with missed ones marked', (tester) async {
    final gateway = FakeGateway(
      eventsValue: [
        event(1, kind: 'call', address: 'Andrew', duration: 240),
        event(2, kind: 'call', address: 'Mom', direction: 'missed'),
      ],
    );
    final (status: _, :inbox) = await _loaded(gateway);
    await tester.pumpWidget(_app(PhonePage(inbox: inbox)));

    expect(find.text('Incoming · 4 min'), findsOneWidget);
    expect(find.text('Missed'), findsOneWidget);
    final missed = tester.widget<Text>(find.text('Mom'));
    expect(missed.style?.color, rackphoneTheme().colorScheme.error);
  });

  testWidgets('notifications name the app, then title and text', (
    tester,
  ) async {
    final gateway = FakeGateway(
      eventsValue: [
        GatewayEvent.fromJson({
          'id': 1,
          'unit': 'lisa01',
          'kind': 'notification',
          'address': 'org.telegram.messenger',
          'body': 'see you at 7',
          'ts': 1700000000000,
          'raw_json': '{"app": "Telegram", "title": "Olga"}',
        }),
        event(2, kind: 'notification', address: 'com.example', body: 'hi'),
      ],
    );
    final (status: _, :inbox) = await _loaded(gateway);
    await tester.pumpWidget(_app(NotificationsPage(inbox: inbox)));

    expect(find.text('Telegram'), findsOneWidget);
    expect(find.text('Olga · see you at 7'), findsOneWidget);
    // Without a label the package is the best name there is.
    expect(find.text('com.example'), findsOneWidget);
  });

  group('screen', () {
    testWidgets('says when the gateway withholds screen access', (
      tester,
    ) async {
      await tester.pumpWidget(
        _app(
          ScreenPage(
            unit: unit(capabilities: {'sms'}),
            createController: null,
            onOpenFiles: null,
          ),
        ),
      );
      expect(
        find.text('This gateway does not allow screen access for lisa01.'),
        findsOneWidget,
      );
    });

    testWidgets('a held screen is distinct from a network failure', (
      tester,
    ) async {
      final held = _PageScreenConnection();
      await tester.pumpWidget(_app(_screenPage(held)));
      await tester.pump();
      held.finish(
        const ScreenCloseReason(
          ScreenCloseKind.heldByAnotherDevice,
          'session_busy Desk tablet',
        ),
      );
      // Two pumps: one for the completer's microtask to reach the controller,
      // one for the rebuild it asks for.
      await tester.pump();
      await tester.pump();
      expect(find.text('Screen is held by Desk tablet.'), findsOneWidget);
      expect(find.textContaining('failed'), findsNothing);

      final failed = _PageScreenConnection();
      await tester.pumpWidget(const SizedBox());
      await tester.pumpWidget(_app(_screenPage(failed)));
      await tester.pump();
      failed.finish(
        const ScreenCloseReason(ScreenCloseKind.networkFailure, 'offline'),
      );
      await tester.pump();
      await tester.pump();
      expect(
        find.textContaining('Screen connection failed: offline'),
        findsOneWidget,
      );
      expect(find.textContaining('held by'), findsNothing);
    });

    testWidgets('the action button unfolds the rarer actions', (tester) async {
      var filesOpened = 0;
      await tester.pumpWidget(
        _app(
          _screenPage(
            _PageScreenConnection(),
            onOpenFiles: () => filesOpened++,
          ),
        ),
      );
      await tester.pump();
      expect(find.text('Files'), findsNothing);

      await tester.tap(find.byTooltip('Screen actions'));
      await tester.pump();
      expect(find.text('Disconnect'), findsOneWidget);

      await tester.tap(find.text('Files'));
      await tester.pump();
      expect(filesOpened, 1);
      expect(find.text('Disconnect'), findsNothing);
    });

    testWidgets('disconnecting offers to connect again', (tester) async {
      var created = 0;
      await tester.pumpWidget(
        _app(
          ScreenPage(
            unit: unit(capabilities: {'screen'}),
            createController: () {
              created++;
              return ScreenController(
                socketFactory: () async => _PageScreenConnection(),
                decoder: FakeScreenDecoder(),
              );
            },
            onOpenFiles: null,
          ),
        ),
      );
      await tester.pump();

      await tester.tap(find.byTooltip('Screen actions'));
      await tester.pump();
      await tester.tap(find.text('Disconnect'));
      // Cancelling a subscription hands back a root-zone future that the fake
      // clock never completes, so let the real event loop turn once.
      await tester.runAsync(() => Future<void>.delayed(Duration.zero));
      await tester.pump();
      expect(find.textContaining('Disconnected'), findsOneWidget);

      await tester.tap(find.byTooltip('Screen actions'));
      await tester.pump();
      await tester.tap(find.text('Connect'));
      await tester.pump();
      expect(created, 2);
    });
  });

  group('dial pad', () {
    testWidgets('typed digits are called from the unit', (tester) async {
      final called = <String>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) =>
                        DialpadPage(unit: 'lisa01', onCall: called.add),
                  ),
                ),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      expect(find.text('Call from lisa01'), findsOneWidget);

      await tester.longPress(find.text('0'));
      for (final key in ['7', '9', '0', '0']) {
        await tester.tap(find.text(key));
      }
      await tester.pump();
      expect(find.text('+7900'), findsOneWidget);

      await tester.tap(find.byTooltip('Call'));
      await tester.pumpAndSettle();
      expect(called, ['+7900']);
      expect(find.text('Call from lisa01'), findsNothing);
    });

    testWidgets('menu keys are not a number to call', (tester) async {
      final called = <String>[];
      await tester.pumpWidget(
        MaterialApp(
          home: DialpadPage(unit: 'lisa01', onCall: called.add),
        ),
      );
      for (final key in ['*', '1', '0', '2', '#']) {
        await tester.tap(find.text(key));
      }
      await tester.tap(find.byTooltip('Call'));
      await tester.pump();
      expect(called, isEmpty);
    });
  });

  testWidgets('file deletion asks before removing the only copy', (
    tester,
  ) async {
    final gateway = FakeGateway();
    final controller = FilesController(gateway: gateway, unit: 'lisa01');
    await tester.pumpWidget(
      MaterialApp(home: FilesPage(controller: controller)),
    );
    await tester.pump();

    await tester.tap(find.byTooltip('Delete payload.bin'));
    await tester.pump();
    expect(find.text('Delete payload.bin?'), findsOneWidget);
    expect(gateway.removed, isEmpty);

    await tester.tap(find.text('Delete'));
    await tester.pump();
    expect(gateway.removed, ['payload.bin']);
  });

  group('settings', () {
    testWidgets('a switch saves the whole notification policy', (tester) async {
      final saved = <ServiceSettings>[];
      final session = await _signedIn(FakeGateway());
      await tester.pumpWidget(
        MaterialApp(
          home: SettingsPage(
            session: session,
            loadSettings: () async => const ServiceSettings(),
            saveSettings: (settings) async => saved.add(settings),
          ),
        ),
      );
      await tester.pump();

      await tester.tap(find.text('App notifications'));
      await tester.pump();

      expect(saved.single.notifyOnNotifications, isTrue);
      expect(saved.single.notifyOnSms, isTrue);
    });

    testWidgets('shows the account and the gateway security posture', (
      tester,
    ) async {
      final session = await _signedIn(
        FakeGateway(statsValue: makeStats(totp: false)),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: SettingsPage(
            session: session,
            loadSettings: () async => const ServiceSettings(),
            saveSettings: (_) async {},
          ),
        ),
      );
      await tester.pump();

      expect(find.text('https://rack.example/'), findsOneWidget);
      expect(find.text('test · control access'), findsOneWidget);
      await tester.scrollUntilVisible(find.textContaining('only barrier'), 200);
      expect(find.textContaining('only barrier'), findsOneWidget);
      await tester.scrollUntilVisible(find.text('9.9.9'), 200);
      expect(find.text('9.9.9'), findsOneWidget);
    });

    testWidgets('a control session is told where security is managed', (
      tester,
    ) async {
      final session = await _signedIn(FakeGateway());
      await tester.pumpWidget(_settings(session));
      await tester.pump();

      await tester.scrollUntilVisible(
        find.text('Sessions and two-factor sign-in'),
        200,
      );
      expect(find.text('Sessions'), findsNothing);
    });

    testWidgets('an admin session lists and revokes sessions', (tester) async {
      final now = DateTime.now().millisecondsSinceEpoch ~/ 1000;
      final gateway = FakeGateway()
        ..scopeGranted = 'admin'
        ..sessionsValue = [
          Session(
            id: 1,
            deviceLabel: 'Pixel 8',
            scope: 'admin',
            issuedAt: now - 100,
            expiresAt: now + 1000,
            lastSeen: now,
            revokedAt: null,
          ),
          Session(
            id: 2,
            deviceLabel: 'Old tablet',
            scope: 'control',
            issuedAt: now - 100,
            expiresAt: now + 1000,
            lastSeen: now - 50,
            revokedAt: null,
          ),
          Session(
            id: 3,
            deviceLabel: 'Revoked phone',
            scope: 'control',
            issuedAt: now - 100,
            expiresAt: now + 1000,
            lastSeen: now - 60,
            revokedAt: now - 10,
          ),
        ];
      final session = await _signedIn(gateway, scope: 'admin');
      await tester.pumpWidget(_settings(session));
      await tester.pump();

      await tester.scrollUntilVisible(find.text('Sessions'), 200);
      await tester.tap(find.text('Sessions'));
      await tester.pumpAndSettle();
      expect(find.text('Old tablet'), findsOneWidget);
      expect(find.text('Revoked phone'), findsNothing);

      await tester.tap(find.byTooltip('Sign out Old tablet'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Sign out'));
      await tester.pumpAndSettle();
      expect(gateway.revoked, [2]);
    });

    testWidgets('turning on two-factor shows the secret once', (tester) async {
      final gateway = FakeGateway(statsValue: makeStats(totp: false))
        ..scopeGranted = 'admin';
      final session = await _signedIn(gateway, scope: 'admin');
      await tester.pumpWidget(_settings(session));
      await tester.pump();

      await tester.scrollUntilVisible(
        find.text('Two-factor authentication'),
        200,
      );
      await tester.tap(find.text('Two-factor authentication'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'secret');
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();

      expect(gateway.totpPasswords, ['secret']);
      Finder selectable(String text) => find.byWidgetPredicate(
        (widget) =>
            widget is SelectableText && (widget.data?.contains(text) ?? false),
      );
      expect(selectable('JBSWY3DPEHPK3PXP'), findsOneWidget);
      expect(selectable('aaaa-1111'), findsOneWidget);
    });

    testWidgets('sign-out asks first', (tester) async {
      final session = await _signedIn(FakeGateway());
      await tester.pumpWidget(
        MaterialApp(
          home: SettingsPage(
            session: session,
            loadSettings: () async => const ServiceSettings(),
            saveSettings: (_) async {},
          ),
        ),
      );
      await tester.pump();

      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      expect(find.text('Sign out?'), findsOneWidget);
      expect(session.state.status, SessionStatus.signedIn);

      await tester.tap(find.widgetWithText(FilledButton, 'Sign out'));
      await tester.pumpAndSettle();
      expect(session.state.status, SessionStatus.signedOut);
    });
  });
}

Future<({UnitStatusController status, InboxController inbox})> _loaded(
  FakeGateway gateway,
) async {
  final status = UnitStatusController(gateway: gateway, unit: 'lisa01');
  final inbox = InboxController(gateway: gateway, unit: unit());
  await Future.wait(<Future<void>>[status.refresh(), inbox.load()]);
  return (status: status, inbox: inbox);
}

Future<SessionController> _signedIn(
  FakeGateway gateway, {
  String scope = 'control',
}) async {
  final session = SessionController(
    tokenStore: InMemoryTokenStore(),
    gatewayFactory: (_) => gateway,
  );
  await session.signIn(
    baseUrl: Uri.parse('https://rack.example/'),
    username: 'admin',
    password: 'secret',
    deviceLabel: 'test',
    scope: scope,
  );
  return session;
}

Widget _settings(SessionController session) => MaterialApp(
  home: SettingsPage(
    session: session,
    loadSettings: () async => const ServiceSettings(),
    saveSettings: (_) async {},
  ),
);

Widget _app(Widget child) => MaterialApp(
  theme: rackphoneTheme(),
  home: Scaffold(body: child),
);

ScreenPage _screenPage(
  _PageScreenConnection connection, {
  VoidCallback? onOpenFiles,
}) => ScreenPage(
  unit: unit(capabilities: {'screen'}),
  createController: () => ScreenController(
    socketFactory: () async => connection,
    decoder: FakeScreenDecoder(),
  ),
  onOpenFiles: onOpenFiles,
);

final class _PageScreenConnection implements ScreenConnection {
  final _closed = Completer<ScreenCloseReason>();

  void finish(ScreenCloseReason reason) => _closed.complete(reason);

  @override
  Future<ScreenCloseReason> get closed => _closed.future;
  @override
  Stream<DeviceInfo> get device => const Stream.empty();
  @override
  Stream<VideoPacket> get video => const Stream.empty();
  @override
  Future<void> close() async {}
  @override
  void send(List<int> controlMessage) {}
}
