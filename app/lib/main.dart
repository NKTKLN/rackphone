import 'package:flutter/material.dart';

import 'src/control.dart';
import 'src/ui/home_page.dart';

void main() {
  runApp(const CompanionApp(control: MethodChannelControl()));
}

/// The Rackphone companion: the part of a unit that is allowed to send.
class CompanionApp extends StatelessWidget {
  const CompanionApp({required this.control, super.key});

  final CompanionControl control;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Rackphone',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      home: HomePage(control: control),
    );
  }

  ThemeData _theme(Brightness brightness) => ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(
      seedColor: const Color(0xFF3DDC84),
      brightness: brightness,
    ),
  );
}
