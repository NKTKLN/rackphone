import 'package:flutter/material.dart';

import '../api/models.dart';

/// Puts the whole rack in reach before anything is chosen.
///
/// A row rather than a dropdown: with a handful of units the choice and the
/// roster are the same information, and hiding it behind a tap would cost a
/// glance every time.
class UnitBar extends StatelessWidget implements PreferredSizeWidget {
  const UnitBar({
    required this.units,
    required this.selectedName,
    required this.onSelect,
    super.key,
  });

  final List<RackUnit> units;
  final String? selectedName;
  final ValueChanged<String> onSelect;

  @override
  Size get preferredSize => const Size.fromHeight(72);

  @override
  Widget build(BuildContext context) => SizedBox(
    height: preferredSize.height,
    child: ListView.separated(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      itemCount: units.length,
      separatorBuilder: (_, _) => const SizedBox(width: 8),
      itemBuilder: (context, index) {
        final unit = units[index];
        final selected = unit.name == selectedName;
        final scheme = Theme.of(context).colorScheme;
        return Material(
          color: selected ? scheme.secondaryContainer : Colors.transparent,
          borderRadius: BorderRadius.circular(14),
          child: InkWell(
            onTap: () => onSelect(unit.name),
            borderRadius: BorderRadius.circular(14),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    unit.name,
                    style: Theme.of(context).textTheme.titleSmall?.copyWith(
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                    ),
                  ),
                  // The unit's own label, not its liveness: nothing here knows
                  // yet whether a phone is answering, and a hardcoded status
                  // would state it anyway. Liveness lands with the Data page,
                  // which is the first thing that actually scrapes a unit.
                  if (unit.label != unit.name)
                    Text(
                      unit.label,
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: scheme.onSurfaceVariant,
                      ),
                    ),
                ],
              ),
            ),
          ),
        );
      },
    ),
  );
}
