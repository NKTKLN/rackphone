import 'package:flutter/material.dart';

import 'src/session/session_controller.dart';
import 'src/session/token_store.dart';
import 'src/service/event_service.dart';
import 'src/ui/app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  EventService.initialize();
  runApp(
    RackphoneApp(
      sessionController: SessionController(tokenStore: SecureTokenStore()),
    ),
  );
}
