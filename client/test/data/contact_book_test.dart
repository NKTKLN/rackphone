import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/data/contact_book.dart';

import '../support/fake_gateway.dart';

void main() {
  test('a number is known however it was written', () async {
    final gateway = FakeGateway()
      ..contactsValue = const [
        Contact(
          name: 'Andrew',
          number: '8 (900) 123-45-67',
          normalized: '+79001234567',
        ),
        Contact(name: 'Bank', number: '900'),
      ];
    final book = ContactBook(gateway: gateway, unit: 'lisa01');
    await book.load();

    expect(book.nameFor('+79001234567'), 'Andrew');
    expect(book.nameFor('89001234567'), 'Andrew');
    expect(book.nameFor('900'), 'Bank');
    expect(book.nameFor('+79990000000'), isNull);
    expect(book.nameFor('T-Bank'), isNull);
  });

  test('a stranger is labelled by the number itself', () async {
    final book = ContactBook(gateway: FakeGateway(), unit: 'lisa01');
    await book.load();

    expect(book.label('+7900'), '+7900');
    expect(book.label(null), 'Unknown');
  });

  test('a refused read is state, and a refresh asks the unit again', () async {
    final gateway = FakeGateway()..contactsFailure = StateError('denied');
    final book = ContactBook(gateway: gateway, unit: 'lisa01');
    await book.load();
    expect(book.failure, isA<StateError>());

    gateway.contactsFailure = null;
    await book.load(refresh: true);
    expect(book.failure, isNull);
    expect(gateway.contactReads, [false, true]);
  });

  test('a contact is texted at its normalised number', () {
    const saved = Contact(name: 'A', number: '8 (900) 123-45-67');
    const normalised = Contact(
      name: 'A',
      number: '8 (900) 123-45-67',
      normalized: '+79001234567',
    );
    expect(saved.address, '89001234567');
    expect(normalised.address, '+79001234567');
  });
}
