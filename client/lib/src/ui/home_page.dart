import 'package:flutter/material.dart';

import '../session/session_controller.dart';
import 'unit_bar.dart';

/// Keeps destination state independent from the controller-owned unit choice.
class HomePage extends StatefulWidget {
  const HomePage({required this.controller, super.key});

  final SessionController controller;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  static const _destinations = <NavigationDestination>[
    NavigationDestination(icon: Icon(Icons.data_usage), label: 'Data'),
    NavigationDestination(
      icon: Icon(Icons.notifications_outlined),
      label: 'Notifications',
    ),
    NavigationDestination(
      icon: Icon(Icons.message_outlined),
      label: 'Messages',
    ),
    NavigationDestination(
      icon: Icon(Icons.phone_android_outlined),
      label: 'Screen',
    ),
  ];
  int _destination = 0;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      toolbarHeight: 72,
      titleSpacing: 0,
      title: UnitBar(
        units: widget.controller.state.units,
        selectedName: widget.controller.selectedUnit?.name,
        onSelect: widget.controller.select,
      ),
    ),
    body: Center(
      child: Text(
        _destinations[_destination].label,
        style: Theme.of(context).textTheme.titleMedium?.copyWith(
          color: Theme.of(context).colorScheme.onSurfaceVariant,
        ),
      ),
    ),
    bottomNavigationBar: NavigationBar(
      selectedIndex: _destination,
      onDestinationSelected: (value) => setState(() => _destination = value),
      destinations: _destinations,
    ),
  );
}
