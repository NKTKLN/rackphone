import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/data/sim_book.dart';

import '../support/fake_gateway.dart';

const _beeline = Sim(subId: 1, slot: 0, carrier: 'Beeline');
const _mts = Sim(subId: 2, slot: 1, carrier: 'MTS');

Future<SimBook> _book(UnitSims sims) async {
  final book = SimBook(
    gateway: FakeGateway()..simsValue = sims,
    unit: 'lisa01',
  );
  await book.load();
  return book;
}

void main() {
  test('one SIM leaves the choice to the unit', () async {
    final book = await _book(const UnitSims(sims: [_beeline], defaultSub: 1));
    expect(book.hasChoice, isFalse);
    expect(book.preferredFor('+7900', const []), isNull);
  });

  test('a number keeps the SIM it last used', () async {
    final book = await _book(
      const UnitSims(sims: [_beeline, _mts], defaultSub: 1),
    );
    final history = [
      event(1, address: '+79001234567', sub: 1),
      event(2, kind: 'call', address: '89001234567', sub: 2),
      event(3, address: '+7000', sub: 1),
    ];
    // Matched on the last ten digits, so 8… and +7… are the same number.
    expect(book.preferredFor('+79001234567', history), 2);
  });

  test('a stranger gets the default SIM', () async {
    final book = await _book(
      const UnitSims(sims: [_beeline, _mts], defaultSub: 2),
    );
    expect(book.preferredFor('+7900', [event(1, address: '+7111', sub: 1)]), 2);
  });

  test('a SIM that is gone is not chosen for a number', () async {
    final book = await _book(
      const UnitSims(sims: [_beeline, _mts], defaultSub: 1),
    );
    expect(book.preferredFor('+7900', [event(1, address: '+7900', sub: 9)]), 1);
  });

  test('the tray number and carrier name a SIM', () {
    expect(_mts.name, 'SIM 2 · MTS');
    expect(const Sim(subId: 3).name, 'SIM');
    expect(const Sim(subId: 3, slot: 0, label: 'Work').name, 'SIM 1 · Work');
  });
}
