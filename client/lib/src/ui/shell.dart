import 'dart:async';

import 'package:flutter/material.dart';

import '../api/gateway_client.dart';
import '../api/models.dart';
import '../data/contact_book.dart';
import '../data/files_controller.dart';
import '../data/inbox_controller.dart';
import '../data/unit_status_controller.dart';
import '../screen/decoder.dart';
import '../screen/screen_controller.dart';
import '../session/session_controller.dart';
import 'pages/dialpad_page.dart';
import 'pages/files_page.dart';
import 'pages/home_page.dart';
import 'pages/messages_page.dart';
import 'pages/phone_page.dart';
import 'pages/screen_page.dart';
import 'pages/settings_page.dart';
import 'search.dart';

enum Destination { home, messages, phone, notifications, screen }

/// The signed-in app: a drawer of destinations over one selected unit.
///
/// The shell owns the per-unit controllers, so choosing another unit replaces
/// them all at once and nothing keeps listening to the unit left behind.
class AppShell extends StatefulWidget {
  const AppShell({
    required this.session,
    this.onCall,
    this.screenControllerFactory,
    super.key,
  });

  final SessionController session;

  /// Places a call from a unit; null when this device cannot carry one.
  final void Function(String unit, String address)? onCall;

  /// Overrides the hardware-backed screen controller in tests.
  final ScreenController Function(GatewayApi gateway, String unit)?
  screenControllerFactory;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  Destination _destination = Destination.home;
  String? _unit;
  InboxController? _inbox;
  ContactBook? _contacts;
  UnitStatusController? _status;
  String? _screenStatus;

  @override
  void initState() {
    super.initState();
    widget.session.addListener(_sessionChanged);
    _sessionChanged();
  }

  @override
  void didUpdateWidget(covariant AppShell oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.session, widget.session)) return;
    oldWidget.session.removeListener(_sessionChanged);
    widget.session.addListener(_sessionChanged);
    _sessionChanged();
  }

  @override
  void dispose() {
    widget.session.removeListener(_sessionChanged);
    _disposeUnit();
    super.dispose();
  }

  void _sessionChanged() {
    final unit = widget.session.selectedUnit;
    final gateway = widget.session.gateway;
    if (unit?.name == _unit && _inbox != null) {
      if (mounted) setState(() {});
      return;
    }
    _disposeUnit();
    _unit = unit?.name;
    if (unit != null && gateway != null) {
      _inbox = InboxController(gateway: gateway, unit: unit)..listen();
      _status = UnitStatusController(gateway: gateway, unit: unit.name);
      unawaited(_inbox!.load());
      unawaited(_status!.refresh());
      final book = gateway.addressBook;
      if (book != null && unit.can('sms')) {
        _contacts = ContactBook(gateway: book, unit: unit.name);
        unawaited(_contacts!.load());
      }
    }
    if (!_available(_destination, unit)) _destination = Destination.home;
    if (mounted) setState(() {});
  }

  void _disposeUnit() {
    _inbox?.dispose();
    _status?.dispose();
    _contacts?.dispose();
    _inbox = null;
    _contacts = null;
    _status = null;
    _screenStatus = null;
  }

  static bool _available(Destination destination, RackUnit? unit) =>
      switch (destination) {
        Destination.home => true,
        Destination.messages || Destination.phone => unit?.can('sms') ?? false,
        Destination.notifications => unit?.can('notifications') ?? false,
        Destination.screen => unit?.can('screen') ?? false,
      };

  static InboxKind? _kindOf(Destination destination) => switch (destination) {
    Destination.messages => InboxKind.messages,
    Destination.phone => InboxKind.calls,
    Destination.notifications => InboxKind.notifications,
    _ => null,
  };

  void _go(Destination destination) {
    if (destination == _destination) return;
    // What was on screen has now been seen. Marking on the way out rather than
    // in keeps the new ones highlighted while the operator reads them.
    final left = _kindOf(_destination);
    if (left != null && left != InboxKind.messages) _inbox?.markSeen(left);
    setState(() => _destination = destination);
  }

  void _openFromHome(InboxKind kind) => _go(switch (kind) {
    InboxKind.messages => Destination.messages,
    InboxKind.calls => Destination.phone,
    InboxKind.notifications => Destination.notifications,
  });

  Future<void> _openThread(MessageThread thread) =>
      _openConversation(thread.address);

  /// Opens the conversation with an address, whether or not one exists yet.
  Future<void> _openConversation(String address) async {
    final inbox = _inbox;
    if (inbox == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) =>
            ThreadPage(inbox: inbox, address: address, contacts: _contacts),
      ),
    );
  }

  /// Calls from the selected unit, when it may and this device can.
  ValueChanged<String>? get _caller {
    final unit = widget.session.selectedUnit;
    final call = widget.onCall;
    if (unit == null || call == null || !unit.can('calls')) return null;
    return (address) => call(unit.name, address);
  }

  Future<void> _openDialpad() async {
    final unit = widget.session.selectedUnit;
    final call = _caller;
    if (unit == null || call == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => DialpadPage(unit: unit.name, onCall: call),
      ),
    );
  }

  Future<void> _newConversation() async {
    final inbox = _inbox;
    if (inbox == null) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(builder: (_) => NewMessagePage(inbox: inbox)),
    );
  }

  Future<void> _openFiles() async {
    final unit = widget.session.selectedUnit;
    final gateway = widget.session.gateway;
    if (unit == null || gateway == null || !unit.can('files')) return;
    final controller = FilesController(gateway: gateway, unit: unit.name);
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => FilesPage(controller: controller),
      ),
    );
    controller.dispose();
  }

  Future<void> _openSettings() => Navigator.of(context).push<void>(
    MaterialPageRoute<void>(
      builder: (_) => SettingsPage(session: widget.session),
    ),
  );

  Future<void> _search() async {
    final inbox = _inbox;
    if (inbox == null) return;
    final thread = await showSearch<MessageThread?>(
      context: context,
      delegate: InboxSearch(
        inbox: inbox,
        contacts: _contacts,
        calls: _destination == Destination.phone,
      ),
    );
    if (thread != null) await _openThread(thread);
  }

  void _screenStatusChanged(String? status) {
    if (!mounted || status == _screenStatus) return;
    setState(() => _screenStatus = status);
  }

  @override
  Widget build(BuildContext context) {
    final unit = widget.session.selectedUnit;
    final inbox = _inbox;
    final status = _status;
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 0,
        title: unit == null || status == null
            ? const Text('Rackphone')
            : ListenableBuilder(
                listenable: status,
                builder: (context, _) => _UnitTitle(
                  name: unit.name,
                  status: _destination == Destination.screen
                      ? _screenStatus ?? status.summary
                      : status.summary,
                  online:
                      status.telemetry?.up == true && status.failure == null,
                ),
              ),
        actions: <Widget>[
          if (_destination == Destination.messages ||
              _destination == Destination.phone)
            IconButton(
              tooltip: 'Search',
              onPressed: _search,
              icon: const Icon(Icons.search),
            ),
        ],
      ),
      drawer: inbox == null || status == null
          ? null
          : ListenableBuilder(
              listenable: Listenable.merge(<Listenable>[inbox, status]),
              builder: (context, _) => _Drawer(
                session: widget.session,
                inbox: inbox,
                status: status,
                destination: _destination,
                available: (destination) => _available(destination, unit),
                onSelect: (destination) {
                  Navigator.of(context).pop();
                  _go(destination);
                },
                onFiles: unit?.can('files') == true
                    ? () {
                        Navigator.of(context).pop();
                        unawaited(_openFiles());
                      }
                    : null,
                onSettings: () {
                  Navigator.of(context).pop();
                  unawaited(_openSettings());
                },
              ),
            ),
      body: _body(unit),
      floatingActionButton: switch (_destination) {
        Destination.messages when inbox?.canSend == true =>
          FloatingActionButton(
            tooltip: 'New conversation',
            onPressed: _newConversation,
            child: const Icon(Icons.edit_outlined),
          ),
        Destination.phone when _caller != null => FloatingActionButton(
          tooltip: 'Dial a number',
          onPressed: _openDialpad,
          child: const Icon(Icons.dialpad),
        ),
        _ => null,
      },
    );
  }

  Widget _body(RackUnit? unit) {
    final inbox = _inbox;
    final status = _status;
    final gateway = widget.session.gateway;
    if (unit == null || inbox == null || status == null || gateway == null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const Text('No unit is available.'),
            const SizedBox(height: 12),
            FilledButton.tonal(
              onPressed: widget.session.refreshUnits,
              child: const Text('Refresh'),
            ),
          ],
        ),
      );
    }
    return switch (_destination) {
      Destination.home => HomePage(
        key: ValueKey('home-${unit.name}'),
        status: status,
        inbox: inbox,
        onOpen: _openFromHome,
        onOpenThread: _openThread,
        contacts: _contacts,
        onCall: _caller,
      ),
      Destination.messages => MessagesPage(
        key: ValueKey('messages-${unit.name}'),
        inbox: inbox,
        onOpenThread: _openThread,
        contacts: _contacts,
      ),
      Destination.phone => PhonePage(
        key: ValueKey('phone-${unit.name}'),
        inbox: inbox,
        contacts: _contacts,
        onMessage: inbox.canSend ? _openConversation : null,
        onCall: _caller,
      ),
      Destination.notifications => NotificationsPage(
        key: ValueKey('notifications-${unit.name}'),
        inbox: inbox,
      ),
      Destination.screen => ScreenPage(
        key: ValueKey('screen-${unit.name}'),
        unit: unit,
        createController: () =>
            widget.screenControllerFactory?.call(gateway, unit.name) ??
            ScreenController(
              socketFactory: () async =>
                  ScreenSocketConnection(await gateway.screen(unit.name)),
              decoder: HardwareScreenDecoder(),
            ),
        onOpenFiles: unit.can('files') ? _openFiles : null,
        onStatus: _screenStatusChanged,
      ),
    };
  }
}

class _UnitTitle extends StatelessWidget {
  const _UnitTitle({
    required this.name,
    required this.status,
    required this.online,
  });

  final String name;
  final String status;
  final bool online;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            _Dot(online: online),
            const SizedBox(width: 8),
            Text(name, style: theme.textTheme.titleMedium),
          ],
        ),
        Text(
          status,
          style: theme.textTheme.bodySmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      ],
    );
  }
}

class _Dot extends StatelessWidget {
  const _Dot({required this.online});

  final bool online;

  @override
  Widget build(BuildContext context) => Container(
    width: 8,
    height: 8,
    decoration: BoxDecoration(
      shape: BoxShape.circle,
      color: online
          ? const Color(0xFF9ED39E)
          : Theme.of(context).colorScheme.outline,
    ),
  );
}

class _Drawer extends StatelessWidget {
  const _Drawer({
    required this.session,
    required this.status,
    required this.inbox,
    required this.destination,
    required this.available,
    required this.onSelect,
    required this.onFiles,
    required this.onSettings,
  });

  final SessionController session;
  final UnitStatusController status;
  final InboxController inbox;
  final Destination destination;
  final bool Function(Destination destination) available;
  final ValueChanged<Destination> onSelect;
  final VoidCallback? onFiles;
  final VoidCallback onSettings;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    Widget item(
      Destination value,
      IconData icon,
      String label, [
      int count = 0,
    ]) => _DrawerItem(
      icon: icon,
      label: label,
      count: count,
      selected: destination == value,
      onTap: () => onSelect(value),
    );
    return Drawer(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 16),
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: Text(
                'Rackphone',
                style: theme.textTheme.titleSmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ),
            _UnitPicker(session: session, status: status),
            const SizedBox(height: 8),
            item(Destination.home, Icons.home_outlined, 'Home'),
            if (available(Destination.messages))
              item(
                Destination.messages,
                Icons.chat_outlined,
                'Messages',
                inbox.unseen(InboxKind.messages),
              ),
            if (available(Destination.phone))
              item(
                Destination.phone,
                Icons.call_outlined,
                'Phone',
                inbox.unseen(InboxKind.calls),
              ),
            if (available(Destination.notifications))
              item(
                Destination.notifications,
                Icons.notifications_outlined,
                'Notifications',
                inbox.unseen(InboxKind.notifications),
              ),
            if (available(Destination.screen))
              item(Destination.screen, Icons.phone_android_outlined, 'Screen'),
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Divider(),
            ),
            if (onFiles != null)
              _DrawerItem(
                icon: Icons.folder_outlined,
                label: 'Files',
                onTap: onFiles!,
              ),
            _DrawerItem(
              icon: Icons.settings_outlined,
              label: 'Settings',
              onTap: onSettings,
            ),
          ],
        ),
      ),
    );
  }
}

/// The selected unit; with more than one, tapping it lists the others.
class _UnitPicker extends StatelessWidget {
  const _UnitPicker({required this.session, required this.status});

  final SessionController session;
  final UnitStatusController status;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final units = session.state.units;
    final selected = session.selectedUnit;
    if (selected == null) return const SizedBox.shrink();
    final card = Row(
      children: <Widget>[
        _Dot(online: status.telemetry?.up == true && status.failure == null),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(selected.name, style: theme.textTheme.titleSmall),
              Text(
                selected.label == selected.name
                    ? status.summary
                    : '${selected.label} · ${status.summary}',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
        if (units.length > 1) const Icon(Icons.unfold_more),
      ],
    );
    return Material(
      color: theme.colorScheme.surfaceContainerHigh,
      borderRadius: BorderRadius.circular(16),
      clipBehavior: Clip.antiAlias,
      child: units.length > 1
          ? PopupMenuButton<String>(
              tooltip: 'Choose unit',
              initialValue: selected.name,
              onSelected: session.select,
              itemBuilder: (context) => <PopupMenuEntry<String>>[
                for (final unit in units)
                  PopupMenuItem<String>(
                    value: unit.name,
                    child: Text(unit.name),
                  ),
              ],
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
                child: card,
              ),
            )
          : Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
              child: card,
            ),
    );
  }
}

class _DrawerItem extends StatelessWidget {
  const _DrawerItem({
    required this.icon,
    required this.label,
    required this.onTap,
    this.count = 0,
    this.selected = false,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final int count;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = selected
        ? scheme.onSecondaryContainer
        : scheme.onSurfaceVariant;
    return Material(
      color: selected ? scheme.secondaryContainer : Colors.transparent,
      shape: const StadiumBorder(),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: SizedBox(
          height: 56,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 24, 0),
            child: Row(
              children: <Widget>[
                Icon(icon, color: color),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    label,
                    style: Theme.of(
                      context,
                    ).textTheme.labelLarge?.copyWith(color: color),
                  ),
                ),
                if (count > 0)
                  Text(
                    '$count',
                    style: Theme.of(
                      context,
                    ).textTheme.labelLarge?.copyWith(color: color),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
