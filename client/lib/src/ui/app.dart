import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';

import '../service/event_service.dart';
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
  SessionStatus _lastStatus = SessionStatus.unknown;

  @override
  void initState() {
    super.initState();
    widget.sessionController.addListener(_sessionChanged);
    widget.sessionController.restore();
  }

  void _sessionChanged() {
    final status = widget.sessionController.state.status;
    if (status == _lastStatus) return;
    _lastStatus = status;
    if (status == SessionStatus.signedIn) {
      unawaited(_beginLiveDelivery());
    } else if (status == SessionStatus.signedOut) {
      unawaited(_stopLiveDelivery());
    }
  }

  Future<void> _beginLiveDelivery() async {
    try {
      final permission =
          await FlutterForegroundTask.checkNotificationPermission();
      if (permission != NotificationPermission.granted) {
        await FlutterForegroundTask.requestNotificationPermission();
      }
      await EventService.start(widget.sessionController.tokenStore);
    } catch (_) {
      // Delivery setup must not replace a valid signed-in screen with a crash;
      // the next session transition can try the platform boundary again.
    }
  }

  Future<void> _stopLiveDelivery() async {
    try {
      await EventService.stop();
    } catch (_) {
      // A service already removed by Android is the desired signed-out state.
    }
  }

  @override
  void didUpdateWidget(covariant RackphoneApp oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.sessionController == widget.sessionController) return;
    oldWidget.sessionController.removeListener(_sessionChanged);
    widget.sessionController.addListener(_sessionChanged);
    _lastStatus = SessionStatus.unknown;
    _sessionChanged();
  }

  @override
  void dispose() {
    widget.sessionController.removeListener(_sessionChanged);
    super.dispose();
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
