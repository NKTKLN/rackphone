import 'package:flutter/material.dart';

import '../session/session_controller.dart';
import 'home_page.dart';
import 'sign_in_page.dart';
import 'theme.dart';

/// Connects the controller's session lifecycle to the application screens.
class RackphoneApp extends StatefulWidget {
  const RackphoneApp({required this.sessionController, super.key});

  final SessionController sessionController;

  @override
  State<RackphoneApp> createState() => _RackphoneAppState();
}

class _RackphoneAppState extends State<RackphoneApp> {
  @override
  void initState() {
    super.initState();
    widget.sessionController.restore();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Rackphone',
    debugShowCheckedModeBanner: false,
    theme: rackphoneTheme(),
    home: ListenableBuilder(
      listenable: widget.sessionController,
      builder: (context, _) {
        final state = widget.sessionController.state;
        return switch (state.status) {
          SessionStatus.unknown => const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          ),
          SessionStatus.signedOut => SignInPage(
            controller: widget.sessionController,
            failure: state.failure,
          ),
          SessionStatus.signingIn => SignInPage(
            controller: widget.sessionController,
            busy: true,
          ),
          SessionStatus.signedIn => HomePage(
            controller: widget.sessionController,
          ),
          SessionStatus.offline => _OfflinePage(
            controller: widget.sessionController,
          ),
        };
      },
    ),
  );
}

class _OfflinePage extends StatelessWidget {
  const _OfflinePage({required this.controller});

  final SessionController controller;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Center(
      child: FutureBuilder(
        future: controller.tokenStore.read(),
        builder: (context, snapshot) {
          final address = snapshot.data?.baseUrl.toString() ?? 'the gateway';
          return Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  'The gateway at $address could not be reached.',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: controller.restore,
                  child: const Text('Try again'),
                ),
              ],
            ),
          );
        },
      ),
    ),
  );
}
