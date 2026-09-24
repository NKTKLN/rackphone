/// How two spellings of one phone number are recognised as the same.
///
/// Numbers are matched on their last ten digits. A Russian number arrives as
/// +7…, is saved as 8…, and is typed either way; the ten digits after the
/// country or trunk prefix are the part everyone agrees on.
library;

const int _matchDigits = 10;

/// The part of [number] that identifies it, or empty when it has no digits.
String numberKey(String number) {
  final digits = number.replaceAll(RegExp(r'\D'), '');
  return digits.length <= _matchDigits
      ? digits
      : digits.substring(digits.length - _matchDigits);
}

/// What groups an address into one conversation.
///
/// A sender with no digits, such as a bank's name, is only ever itself.
String conversationKey(String? address) {
  final raw = address ?? '';
  final key = numberKey(raw);
  return key.isEmpty ? raw : key;
}
