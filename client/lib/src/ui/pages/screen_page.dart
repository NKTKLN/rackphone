import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../api/models.dart';
import '../../screen/protocol.dart';
import '../../screen/screen_controller.dart';

/// Builds a fresh controller; one that has disconnected cannot reconnect.
typedef ScreenControllerFactory = ScreenController Function();

/// The unit's screen, with the rarer actions tucked into one expanding button.
class ScreenPage extends StatefulWidget {
  const ScreenPage({
    required this.unit,
    required this.createController,
    required this.onOpenFiles,
    this.onStatus,
    super.key,
  });

  final RackUnit unit;
  final ScreenControllerFactory? createController;
  final VoidCallback? onOpenFiles;

  /// Reports the line the top bar shows under the unit's name.
  final ValueChanged<String?>? onStatus;

  @override
  State<ScreenPage> createState() => _ScreenPageState();
}

class _ScreenPageState extends State<ScreenPage> {
  ScreenController? _controller;
  bool _menuOpen = false;

  @override
  void initState() {
    super.initState();
    if (widget.unit.can('screen')) _connect();
  }

  @override
  void dispose() {
    _controller?.removeListener(_reportStatus);
    _controller?.dispose();
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => widget.onStatus?.call(null),
    );
    super.dispose();
  }

  void _connect() {
    final create = widget.createController;
    if (create == null) return;
    _controller?.removeListener(_reportStatus);
    _controller?.dispose();
    final controller = create()..addListener(_reportStatus);
    _controller = controller;
    unawaited(controller.connect());
    WidgetsBinding.instance.addPostFrameCallback((_) => _reportStatus());
  }

  Future<void> _disconnect() async {
    setState(() => _menuOpen = false);
    await _controller?.disconnect();
  }

  void _reportStatus() {
    final controller = _controller;
    if (controller == null || !mounted) return;
    final device = controller.device;
    widget.onStatus?.call(switch (controller.state) {
      ScreenState.live when device != null =>
        'Streaming · ${device.width}×${device.height}',
      ScreenState.live || ScreenState.connecting => 'Connecting…',
      ScreenState.heldByAnotherDevice => 'Held by another device',
      ScreenState.closed => 'Disconnected',
      ScreenState.failed => 'Connection failed',
    });
  }

  Future<void> _fullscreen() async {
    final controller = _controller;
    if (controller == null) return;
    setState(() => _menuOpen = false);
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => _FullscreenScreen(controller: controller),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final unit = widget.unit;
    if (!unit.can('screen')) {
      return _message(
        'This gateway does not allow screen access for ${unit.name}.',
      );
    }
    final controller = _controller;
    if (controller == null) {
      return _message('Screen access for ${unit.name} is not available here.');
    }
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) => Stack(
        children: <Widget>[
          Positioned.fill(child: _body(controller)),
          if (_menuOpen)
            Positioned.fill(
              child: GestureDetector(
                onTap: () => setState(() => _menuOpen = false),
                child: const ColoredBox(color: Color(0x73000000)),
              ),
            ),
          Positioned(
            right: 16,
            bottom: 16,
            child: _ActionMenu(
              open: _menuOpen,
              onToggle: () => setState(() => _menuOpen = !_menuOpen),
              actions: <_MenuAction>[
                if (controller.state == ScreenState.live) ...<_MenuAction>[
                  _MenuAction(Icons.fullscreen, 'Fullscreen', _fullscreen),
                  _MenuAction(Icons.screen_rotation, 'Rotate', () {
                    setState(() => _menuOpen = false);
                    controller.rotate();
                  }),
                ],
                if (widget.onOpenFiles != null)
                  _MenuAction(Icons.folder_outlined, 'Files', () {
                    setState(() => _menuOpen = false);
                    widget.onOpenFiles!();
                  }),
                if (controller.state == ScreenState.live ||
                    controller.state == ScreenState.connecting)
                  _MenuAction(Icons.logout, 'Disconnect', _disconnect)
                else
                  _MenuAction(Icons.play_arrow, 'Connect', () {
                    setState(() {
                      _menuOpen = false;
                      _connect();
                    });
                  }),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _body(ScreenController controller) {
    if (controller.state == ScreenState.heldByAnotherDevice) {
      final holder = controller.holder;
      return _message(
        holder == null
            ? 'Screen is held by another device.'
            : 'Screen is held by $holder.',
      );
    }
    if (controller.state == ScreenState.closed) {
      return _message('Disconnected. Open the menu to connect again.');
    }
    if (controller.state == ScreenState.failed) {
      return _message(
        'Screen connection failed: ${controller.message ?? 'unknown error'}',
      );
    }
    return ColoredBox(
      color: Colors.black,
      child: Column(
        children: <Widget>[
          Expanded(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: ScreenSurface(controller: controller),
            ),
          ),
          _NavigationKeys(controller: controller),
        ],
      ),
    );
  }

  Widget _message(String text) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Text(text, textAlign: TextAlign.center),
    ),
  );
}

/// The mirrored display, forwarding touches through a contain-fit mapping.
class ScreenSurface extends StatelessWidget {
  const ScreenSurface({required this.controller, super.key});

  final ScreenController controller;

  @override
  Widget build(BuildContext context) {
    final device = controller.device;
    final textureId = controller.textureId;
    if (device == null || textureId == null) {
      return const Center(child: CircularProgressIndicator());
    }
    return Center(
      child: AspectRatio(
        aspectRatio: device.width / device.height,
        child: LayoutBuilder(
          builder: (context, constraints) => Listener(
            onPointerDown: (event) => controller.sendTouch(
              0,
              event.localPosition,
              constraints.biggest,
            ),
            onPointerMove: (event) => controller.sendTouch(
              2,
              event.localPosition,
              constraints.biggest,
            ),
            onPointerUp: (event) => controller.sendTouch(
              1,
              event.localPosition,
              constraints.biggest,
            ),
            child: Texture(textureId: textureId),
          ),
        ),
      ),
    );
  }
}

class _NavigationKeys extends StatelessWidget {
  const _NavigationKeys({required this.controller});

  final ScreenController controller;

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.onSurfaceVariant;
    Widget key(IconData icon, String label, int keycode) => IconButton(
      tooltip: label,
      color: color,
      onPressed: controller.state == ScreenState.live
          ? () => controller.pressKey(keycode)
          : null,
      icon: Icon(icon),
    );
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceEvenly,
      children: <Widget>[
        key(Icons.arrow_back_ios_new, 'Back', AndroidKey.back),
        key(Icons.circle_outlined, 'Home', AndroidKey.home),
        key(Icons.crop_square, 'Recent apps', AndroidKey.appSwitch),
      ],
    );
  }
}

final class _MenuAction {
  const _MenuAction(this.icon, this.label, this.onPressed);

  final IconData icon;
  final String label;
  final VoidCallback onPressed;
}

/// A floating button that unfolds its actions upwards, as Google Keep does.
class _ActionMenu extends StatelessWidget {
  const _ActionMenu({
    required this.open,
    required this.onToggle,
    required this.actions,
  });

  final bool open;
  final VoidCallback onToggle;
  final List<_MenuAction> actions;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.end,
      children: <Widget>[
        if (open)
          for (final action in actions)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: FilledButton.tonalIcon(
                style: FilledButton.styleFrom(
                  backgroundColor: scheme.surfaceContainerHighest,
                  foregroundColor: scheme.onSurface,
                  minimumSize: const Size(0, 48),
                  padding: const EdgeInsets.fromLTRB(16, 0, 20, 0),
                ),
                onPressed: action.onPressed,
                icon: Icon(action.icon),
                label: Text(action.label),
              ),
            ),
        FloatingActionButton(
          heroTag: null,
          tooltip: open ? 'Close menu' : 'Screen actions',
          onPressed: onToggle,
          child: Icon(open ? Icons.close : Icons.more_vert),
        ),
      ],
    );
  }
}

/// Nothing but the picture, with the system bars hidden until it closes.
class _FullscreenScreen extends StatefulWidget {
  const _FullscreenScreen({required this.controller});

  final ScreenController controller;

  @override
  State<_FullscreenScreen> createState() => _FullscreenScreenState();
}

class _FullscreenScreenState extends State<_FullscreenScreen> {
  @override
  void initState() {
    super.initState();
    unawaited(
      SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky),
    );
  }

  @override
  void dispose() {
    unawaited(SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge));
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: Colors.black,
    body: Stack(
      children: <Widget>[
        Positioned.fill(
          child: ListenableBuilder(
            listenable: widget.controller,
            builder: (context, _) =>
                ScreenSurface(controller: widget.controller),
          ),
        ),
        Positioned(
          top: 12,
          right: 12,
          child: SafeArea(
            child: IconButton.filledTonal(
              tooltip: 'Exit fullscreen',
              onPressed: () => Navigator.of(context).pop(),
              icon: const Icon(Icons.fullscreen_exit),
            ),
          ),
        ),
      ],
    ),
  );
}
