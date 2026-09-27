import 'package:flutter/material.dart';

import '../../api/models.dart';
import '../../data/sim_book.dart';
import '../sim_picker.dart';
import 'call_page.dart';

/// Types a number and places the call, as a phone's dialler does.
class DialpadPage extends StatefulWidget {
  const DialpadPage({
    required this.unit,
    required this.onCall,
    this.sims,
    this.history = const <GatewayEvent>[],
    super.key,
  });

  /// Which unit the call goes out from, shown so it is never a surprise.
  final String unit;

  /// Places the call; a null SIM leaves the choice to the unit.
  final void Function(String number, int? sim) onCall;
  final SimBook? sims;

  /// Messages and calls, so a known number keeps the SIM it last used.
  final Iterable<GatewayEvent> history;

  @override
  State<DialpadPage> createState() => _DialpadPageState();
}

class _DialpadPageState extends State<DialpadPage> {
  String _number = '';
  int? _chosenSim;

  int? get _sim =>
      _chosenSim ?? widget.sims?.preferredFor(_number, widget.history);

  void _key(String key) => setState(() => _number += key);

  void _erase() {
    if (_number.isEmpty) return;
    setState(() => _number = _number.substring(0, _number.length - 1));
  }

  /// `+` and the digits are all a number is; `*` and `#` belong to USSD and
  /// to menus once connected, not to a destination.
  bool get _callable => RegExp(r'^\+?[0-9]+$').hasMatch(_number);

  void _call() {
    if (!_callable) return;
    final sim = _sim;
    Navigator.of(context).pop();
    widget.onCall(_number, sim);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text('Call from ${widget.unit}')),
      body: SafeArea(
        child: Column(
          children: <Widget>[
            const Spacer(),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: Row(
                children: <Widget>[
                  const SizedBox(width: 48),
                  Expanded(
                    child: Text(
                      _number,
                      textAlign: TextAlign.center,
                      maxLines: 1,
                      overflow: TextOverflow.fade,
                      softWrap: false,
                      style: theme.textTheme.displaySmall,
                    ),
                  ),
                  IconButton(
                    tooltip: 'Delete',
                    onPressed: _number.isEmpty ? null : _erase,
                    onLongPress: () => setState(() => _number = ''),
                    icon: const Icon(Icons.backspace_outlined),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 32),
              child: Keypad(onKey: _key, onPlus: () => _key('+')),
            ),
            SimPicker(
              book: widget.sims,
              selected: _sim,
              onSelected: (sim) => setState(() => _chosenSim = sim),
            ),
            const SizedBox(height: 16),
            FloatingActionButton.large(
              heroTag: null,
              tooltip: 'Call',
              backgroundColor: const Color(0xFF2E7D32),
              foregroundColor: Colors.white,
              onPressed: _callable ? _call : null,
              child: const Icon(Icons.call),
            ),
            const SizedBox(height: 32),
          ],
        ),
      ),
    );
  }
}
