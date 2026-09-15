import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';

import '../api/gateway_client.dart';
import '../call/audio.dart';
import '../call/call_controller.dart';
import '../service/event_service.dart';
import '../service/notifications.dart';
import '../session/session_controller.dart';
import 'home_page.dart';
import 'pages/call_page.dart';
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
  String? _deliveryFailure;
  CallController? _callController;
  GatewayCallsApi? _callGateway;
  final ArrivalNotifications _notifications = ArrivalNotifications();
  Future<void>? _notificationsReady;

  @override
  void initState() {
    super.initState();
    widget.sessionController.addListener(_sessionChanged);
    widget.sessionController.restore();
  }

  Future<void> _initializeNotifications() async {
    await _notifications.initialize(onCall: EventService.publishCallEvent);
    final launchedCall = await _notifications.callThatLaunchedApp();
    if (launchedCall != null) EventService.publishCallEvent(launchedCall);
  }

  void _sessionChanged() {
    final status = widget.sessionController.state.status;
    if (status == _lastStatus) return;
    _lastStatus = status;
    if (status == SessionStatus.signedIn) {
      _updateCallController();
      unawaited(_beginLiveDelivery());
    } else if (status == SessionStatus.signedOut) {
      _disposeCallController();
      unawaited(_stopLiveDelivery());
    }
  }

  void _updateCallController() {
    final gateway = widget.sessionController.gateway;
    if (gateway is! GatewayCallsApi || identical(gateway, _callGateway)) return;
    final callGateway = gateway as GatewayCallsApi;
    _disposeCallController();
    _callGateway = callGateway;
    _callController = CallController(
      gateway: callGateway,
      events: EventService.callEvents,
      initialEvent: EventService.latestCall,
      audio: HardwareCallAudio(),
    );
    if (mounted) setState(() {});
  }

  void _disposeCallController() {
    _callController?.dispose();
    _callController = null;
    _callGateway = null;
  }

  Future<void> _beginLiveDelivery() async {
    try {
      final permission =
          await FlutterForegroundTask.checkNotificationPermission();
      if (permission != NotificationPermission.granted) {
        await FlutterForegroundTask.requestNotificationPermission();
      }
      await (_notificationsReady ??= _initializeNotifications());
      await _notifications.requestFullScreenPermission();
      await EventService.start(widget.sessionController.tokenStore);
      if (mounted) setState(() => _deliveryFailure = null);
    } catch (failure) {
      // Not swallowed. A crash must not replace a perfectly good signed-in
      // screen, but silence here is worse than either: the app goes on looking
      // connected while nothing will ever arrive, and only a session change
      // would have retried it. Say so, and offer the retry.
      if (mounted) setState(() => _deliveryFailure = '$failure');
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
    _disposeCallController();
    super.dispose();
  }

  /// Puts a failed live connection where it can be seen and retried.
  Widget _withDeliveryBanner(Widget child) {
    final failure = _deliveryFailure;
    if (failure == null) return child;
    return Column(
      children: <Widget>[
        MaterialBanner(
          content: Text('Notifications are not running: $failure'),
          actions: <Widget>[
            TextButton(
              onPressed: () => unawaited(_beginLiveDelivery()),
              child: const Text('Try again'),
            ),
            TextButton(
              onPressed: () => setState(() => _deliveryFailure = null),
              child: const Text('Dismiss'),
            ),
          ],
        ),
        Expanded(child: child),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Rackphone',
    debugShowCheckedModeBanner: false,
    theme: rackphoneTheme(),
    builder: (context, child) {
      final callController = _callController;
      return Stack(
        children: <Widget>[
          ?child,
          if (callController != null) CallOverlay(controller: callController),
        ],
      );
    },
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
          SessionStatus.signedIn => _withDeliveryBanner(
            HomePage(controller: widget.sessionController),
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
