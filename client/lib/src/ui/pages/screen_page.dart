import 'package:flutter/material.dart';

import '../../api/models.dart';

/// Separates unavailable permission from device support that is still absent.
class ScreenPage extends StatelessWidget {
  const ScreenPage({required this.unit, super.key});

  final RackUnit unit;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Text(
        unit.can('screen')
            ? 'Screen access for ${unit.name} will show and control the device when the device-side plugin is built.'
            : 'This gateway does not allow screen access for ${unit.name}.',
        textAlign: TextAlign.center,
      ),
    ),
  );
}
