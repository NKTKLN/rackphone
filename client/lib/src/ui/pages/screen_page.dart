import 'dart:async';

import 'package:flutter/material.dart';

import '../../api/models.dart';
import '../../screen/screen_controller.dart';

/// Separates unavailable permission from device support that is still absent.
class ScreenPage extends StatefulWidget {
  const ScreenPage({required this.unit, this.controller, super.key});

  final RackUnit unit;
  final ScreenController? controller;

  @override
  State<ScreenPage> createState() => _ScreenPageState();
}

class _ScreenPageState extends State<ScreenPage> {
  @override
  void initState() {
    super.initState();
    if (widget.unit.can('screen')) unawaited(widget.controller?.connect());
  }

  @override
  void dispose() {
    unawaited(widget.controller?.disconnect());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final unit = widget.unit;
    if (!unit.can('screen')) {
      return _message(
        'This gateway does not allow screen access for ${unit.name}.',
      );
    }
    final controller = widget.controller;
    if (controller == null) {
      return _message(
        'Screen access for ${unit.name} will show and control the device when the device-side plugin is built.',
      );
    }
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) => _screen(controller),
    );
  }

  Widget _screen(ScreenController controller) {
    if (controller.state == ScreenState.heldByAnotherDevice) {
      final holder = controller.holder;
      return _message(
        holder == null
            ? 'Screen is held by another device.'
            : 'Screen is held by $holder.',
      );
    }
    final device = controller.device;
    final textureId = controller.textureId;
    return Column(
      children: <Widget>[
        if (device != null && textureId != null)
          Expanded(
            child: Center(
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
            ),
          )
        else
          const Spacer(),
        if (controller.state != ScreenState.live)
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(_status(controller), textAlign: TextAlign.center),
          ),
      ],
    );
  }

  String _status(ScreenController controller) => switch (controller.state) {
    ScreenState.connecting => 'Connecting to ${widget.unit.name}…',
    ScreenState.closed => 'Screen connection closed.',
    ScreenState.failed =>
      'Screen connection failed: ${controller.message ?? 'unknown error'}',
    ScreenState.heldByAnotherDevice => '',
    ScreenState.live => '',
  };

  Widget _message(String text) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Text(text, textAlign: TextAlign.center),
    ),
  );
}
