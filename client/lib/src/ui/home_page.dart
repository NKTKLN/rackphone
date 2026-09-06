import 'package:flutter/material.dart';

import '../api/gateway_client.dart';
import '../data/feed_controller.dart';
import '../data/files_controller.dart';
import '../screen/decoder.dart';
import '../screen/screen_controller.dart';
import '../session/session_controller.dart';
import 'pages/feed_page.dart';
import 'pages/files_page.dart';
import 'pages/messages_page.dart';
import 'pages/overview_page.dart';
import 'pages/screen_page.dart';
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
  FeedController? _feedController;
  String? _feedUnit;
  ScreenController? _screenController;
  String? _screenUnit;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _updateFeedController();
    _updateScreenController();
  }

  @override
  void didUpdateWidget(covariant HomePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    _updateFeedController();
    _updateScreenController();
  }

  void _updateScreenController() {
    final unit = widget.controller.selectedUnit;
    final gateway = widget.controller.gateway;
    if (unit == null || gateway == null) return;
    if (_screenUnit == unit.name && _screenController != null) return;
    _screenController?.dispose();
    _screenUnit = unit.name;
    _screenController = ScreenController(
      socketFactory: () async =>
          ScreenSocketConnection(await gateway.screen(unit.name)),
      decoder: HardwareScreenDecoder(),
    );
  }

  void _updateFeedController() {
    final unit = widget.controller.selectedUnit;
    final gateway = widget.controller.gateway;
    if (unit == null || gateway == null) return;
    if (_feedUnit == unit.name && _feedController != null) return;
    _feedController?.dispose();
    _feedUnit = unit.name;
    _feedController = FeedController(gateway: gateway, unit: unit.name);
  }

  @override
  void dispose() {
    _feedController?.dispose();
    super.dispose();
  }

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
      actions: [
        if (widget.controller.selectedUnit?.can('files') == true)
          IconButton(
            tooltip: 'Files',
            onPressed: _openFiles,
            icon: const Icon(Icons.folder_outlined),
          ),
      ],
    ),
    body: _body(),
    bottomNavigationBar: NavigationBar(
      selectedIndex: _destination,
      onDestinationSelected: (value) => setState(() => _destination = value),
      destinations: _destinations,
    ),
  );

  Future<void> _openFiles() async {
    final unit = widget.controller.selectedUnit;
    final gateway = widget.controller.gateway;
    if (unit == null || gateway == null || !unit.can('files')) return;
    final controller = FilesController(gateway: gateway, unit: unit.name);
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => FilesPage(controller: controller),
      ),
    );
    controller.dispose();
  }

  Widget _body() {
    final unit = widget.controller.selectedUnit;
    final gateway = widget.controller.gateway;
    final feed = _feedController;
    if (unit == null || gateway == null || feed == null) {
      return const Center(
        child: Text(
          'No unit is available. Refresh the unit list to try again.',
        ),
      );
    }
    return switch (_destination) {
      0 => OverviewPage(
        key: ValueKey('overview-${unit.name}'),
        gateway: gateway,
        unit: unit,
      ),
      1 => FeedPage(key: ValueKey('feed-${unit.name}'), controller: feed),
      2 => MessagesPage(
        key: ValueKey('messages-${unit.name}'),
        controller: feed,
      ),
      _ => ScreenPage(
        key: ValueKey('screen-${unit.name}'),
        unit: unit,
        controller: _screenController,
      ),
    };
  }
}
