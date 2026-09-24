import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/session/token_store.dart';

void main() {
  const secureStorage = FlutterSecureStorage();

  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
  });

  test('in-memory store reads, writes, and clears a session', () async {
    final store = InMemoryTokenStore();
    final session = _session();

    expect(await store.read(), isNull);
    await store.write(session);
    expect(await store.read(), session);
    await store.clear();
    expect(await store.read(), isNull);
  });

  test('secure store round trips the durable fields', () async {
    final store = SecureTokenStore(secureStorage);
    final session = _session();

    await store.write(session);

    expect(await store.read(), session);
  });

  test(
    'secure store clears an incomplete record instead of throwing',
    () async {
      FlutterSecureStorage.setMockInitialValues({
        'rackphone.session': '{"base_url":"https://rack.example"}',
      });
      final store = SecureTokenStore(secureStorage);

      expect(await store.read(), isNull);
      expect(await secureStorage.read(key: 'rackphone.session'), isNull);
    },
  );

  test('secure store clears malformed JSON instead of throwing', () async {
    FlutterSecureStorage.setMockInitialValues({'rackphone.session': '{broken'});
    final store = SecureTokenStore(secureStorage);

    expect(await store.read(), isNull);
    expect(await secureStorage.read(key: 'rackphone.session'), isNull);
  });
}

StoredSession _session() => StoredSession(
  baseUrl: Uri.parse('https://rack.example/'),
  refreshToken: 'refresh-secret',
  deviceLabel: 'operator phone',
  scope: 'control',
  refreshExpiresAt: 123456,
);
