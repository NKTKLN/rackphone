import 'dart:async';

import 'package:flutter/material.dart';

import '../../call/call_controller.dart';

/// A dialer-like surface that deliberately covers every app destination.
class CallOverlay extends StatelessWidget {
  const CallOverlay({required this.controller, super.key});

  final CallController controller;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) => switch (controller.state) {
      CallState.idle => const SizedBox.shrink(),
      CallState.ringing => _IncomingCall(controller: controller),
      CallState.connecting ||
      CallState.inCall => _ActiveCall(controller: controller),
      CallState.ended => _EndedCall(controller: controller),
    },
  );
}

class _IncomingCall extends StatelessWidget {
  const _IncomingCall({required this.controller});

  final CallController controller;

  @override
  Widget build(BuildContext context) => _CallSurface(
    children: <Widget>[
      const Icon(Icons.phone_in_talk, size: 76),
      const SizedBox(height: 28),
      Text('Incoming call', style: Theme.of(context).textTheme.titleLarge),
      const SizedBox(height: 12),
      Text(
        controller.caller ?? 'Unknown',
        textAlign: TextAlign.center,
        style: Theme.of(context).textTheme.headlineMedium,
      ),
      const SizedBox(height: 8),
      Text(controller.unit ?? '', style: Theme.of(context).textTheme.bodyLarge),
      const Spacer(),
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: <Widget>[
          _RoundCallButton(
            label: 'Reject',
            icon: Icons.call_end,
            color: Theme.of(context).colorScheme.error,
            onPressed: () => unawaited(controller.reject()),
          ),
          _RoundCallButton(
            label: 'Accept',
            icon: Icons.call,
            color: Colors.green.shade700,
            onPressed: () => unawaited(controller.accept()),
          ),
        ],
      ),
    ],
  );
}

class _ActiveCall extends StatefulWidget {
  const _ActiveCall({required this.controller});

  final CallController controller;

  @override
  State<_ActiveCall> createState() => _ActiveCallState();
}

class _ActiveCallState extends State<_ActiveCall> {
  Timer? _timer;
  bool _keypad = false;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final startedAt = controller.startedAt;
    final elapsed = startedAt == null
        ? Duration.zero
        : DateTime.now().difference(startedAt);
    final minutes = elapsed.inMinutes.toString().padLeft(2, '0');
    final seconds = (elapsed.inSeconds % 60).toString().padLeft(2, '0');
    final connected = controller.state == CallState.inCall;
    final theme = Theme.of(context);
    return _CallSurface(
      children: <Widget>[
        if (!_keypad) ...<Widget>[
          const Icon(Icons.account_circle, size: 104),
          const SizedBox(height: 24),
        ],
        Text(
          controller.caller ?? 'Unknown',
          textAlign: TextAlign.center,
          style: theme.textTheme.headlineMedium,
        ),
        const SizedBox(height: 8),
        Text(
          controller.state == CallState.connecting
              ? (controller.outgoing ? 'Calling…' : 'Connecting…')
              : '$minutes:$seconds',
          style: theme.textTheme.titleLarge,
        ),
        if (controller.unit != null)
          Text(
            'via ${controller.unit}',
            style: theme.textTheme.bodyMedium?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        if (controller.message?.isNotEmpty == true)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              controller.message!,
              textAlign: TextAlign.center,
              style: TextStyle(color: theme.colorScheme.error),
            ),
          ),
        const Spacer(),
        if (_keypad) ...<Widget>[
          Text(
            controller.keys,
            style: theme.textTheme.headlineSmall,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 8),
          Keypad(onKey: (key) => unawaited(controller.press(key))),
          const SizedBox(height: 16),
        ],
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
          children: <Widget>[
            _RoundCallButton(
              label: controller.muted ? 'Unmute' : 'Mute',
              icon: controller.muted ? Icons.mic_off : Icons.mic,
              color: theme.colorScheme.secondaryContainer,
              onPressed: connected ? controller.toggleMute : null,
            ),
            _RoundCallButton(
              label: 'Keypad',
              icon: _keypad ? Icons.dialpad : Icons.dialpad_outlined,
              color: _keypad
                  ? theme.colorScheme.primaryContainer
                  : theme.colorScheme.secondaryContainer,
              onPressed: connected
                  ? () => setState(() => _keypad = !_keypad)
                  : null,
            ),
            _RoundCallButton(
              label: 'Hang up',
              icon: Icons.call_end,
              color: Theme.of(context).colorScheme.error,
              onPressed: () => unawaited(controller.hangup()),
            ),
          ],
        ),
      ],
    );
  }
}

class _EndedCall extends StatelessWidget {
  const _EndedCall({required this.controller});

  final CallController controller;

  @override
  Widget build(BuildContext context) => _CallSurface(
    children: <Widget>[
      const Spacer(),
      const Icon(Icons.call_end, size: 72),
      const SizedBox(height: 24),
      Text('Call ended', style: Theme.of(context).textTheme.headlineMedium),
      if (controller.message?.isNotEmpty == true) ...<Widget>[
        const SizedBox(height: 12),
        Text(controller.message!, textAlign: TextAlign.center),
      ],
      const Spacer(),
      FilledButton(
        onPressed: controller.dismissEnded,
        child: const Text('Close'),
      ),
    ],
  );
}

class _CallSurface extends StatelessWidget {
  const _CallSurface({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Positioned.fill(
    child: Material(
      color: Theme.of(context).colorScheme.surface,
      child: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(32, 56, 32, 48),
          child: Column(children: children),
        ),
      ),
    ),
  );
}

class _RoundCallButton extends StatelessWidget {
  const _RoundCallButton({
    required this.label,
    required this.icon,
    required this.color,
    required this.onPressed,
  });

  final String label;
  final IconData icon;
  final Color color;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    children: <Widget>[
      IconButton.filled(
        onPressed: onPressed,
        style: IconButton.styleFrom(
          backgroundColor: color,
          minimumSize: const Size.square(72),
        ),
        iconSize: 34,
        icon: Icon(icon),
      ),
      const SizedBox(height: 10),
      Text(label),
    ],
  );
}

/// The twelve keys of a phone, each with its letters, as every dialler has.
class Keypad extends StatelessWidget {
  const Keypad({required this.onKey, this.onPlus, super.key});

  final ValueChanged<String> onKey;

  /// A long press on 0, which is how a phone types `+`; null disables it.
  final VoidCallback? onPlus;

  static const _keys = <(String, String)>[
    ('1', ''),
    ('2', 'ABC'),
    ('3', 'DEF'),
    ('4', 'GHI'),
    ('5', 'JKL'),
    ('6', 'MNO'),
    ('7', 'PQRS'),
    ('8', 'TUV'),
    ('9', 'WXYZ'),
    ('*', ''),
    ('0', '+'),
    ('#', ''),
  ];

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    // A thumb's width of keys, however wide the screen.
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 320),
      child: _grid(theme),
    );
  }

  Widget _grid(ThemeData theme) {
    return GridView.count(
      crossAxisCount: 3,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      childAspectRatio: 1.6,
      children: <Widget>[
        for (final (digit, letters) in _keys)
          InkWell(
            customBorder: const StadiumBorder(),
            onTap: () => onKey(digit),
            onLongPress: digit == '0' ? onPlus : null,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: <Widget>[
                Text(digit, style: theme.textTheme.headlineMedium),
                Text(
                  letters,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
