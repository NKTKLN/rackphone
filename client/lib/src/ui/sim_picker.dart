import 'package:flutter/material.dart';

import '../data/sim_book.dart';

/// Which SIM a message or call goes out on, as a row of chips.
///
/// Draws nothing on a unit with one SIM, or before its list has loaded:
/// there the unit's own default is the only answer anyway.
class SimPicker extends StatelessWidget {
  const SimPicker({
    required this.book,
    required this.selected,
    required this.onSelected,
    this.enabled = true,
    super.key,
  });

  final SimBook? book;
  final int? selected;
  final ValueChanged<int> onSelected;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final book = this.book;
    if (book == null) return const SizedBox.shrink();
    return ListenableBuilder(
      listenable: book,
      builder: (context, _) {
        if (!book.hasChoice) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
          child: Wrap(
            spacing: 8,
            runSpacing: 4,
            children: <Widget>[
              for (final sim in book.sims)
                ChoiceChip(
                  avatar: const Icon(Icons.sim_card_outlined, size: 18),
                  label: Text(sim.name),
                  selected: sim.subId == selected,
                  onSelected: enabled ? (_) => onSelected(sim.subId) : null,
                ),
            ],
          ),
        );
      },
    );
  }
}
