import 'package:flutter/foundation.dart';

import '../api/gateway_client.dart';
import '../api/models.dart';
import 'numbers.dart';

/// One unit's address book, and the names it gives to numbers, matched as
/// [numberKey] matches them.
final class ContactBook extends ChangeNotifier {
  ContactBook({required this.gateway, required this.unit});

  final GatewayContactsApi gateway;
  final String unit;
  List<Contact> _contacts = const [];
  Map<String, String> _names = const {};
  bool _loading = false;
  bool _disposed = false;
  Object? _failure;

  List<Contact> get contacts => _contacts;
  bool get loading => _loading;
  Object? get failure => _failure;

  Future<void> load({bool refresh = false}) async {
    _loading = true;
    notifyListeners();
    try {
      final contacts = await gateway.contacts(unit, refresh: refresh);
      _contacts = List.unmodifiable(contacts);
      _names = {
        for (final contact in contacts.reversed)
          for (final number in <String>[contact.number, ?contact.normalized])
            if (numberKey(number).isNotEmpty) numberKey(number): contact.name,
      };
      _failure = null;
    } catch (failure) {
      _failure = failure;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  /// The saved name for an address, or null for a stranger.
  String? nameFor(String? address) {
    if (address == null) return null;
    final key = numberKey(address);
    return key.isEmpty ? null : _names[key];
  }

  /// What to call an address in a list: its name, else itself.
  String label(String? address) {
    final name = nameFor(address);
    if (name != null) return name;
    return address == null || address.isEmpty ? 'Unknown' : address;
  }

  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
