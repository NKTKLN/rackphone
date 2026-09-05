import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Durable credentials are an interface so session behavior can be exercised
/// without an Android Keystore underneath it.
abstract class TokenStore {
  Future<StoredSession?> read();

  Future<void> write(StoredSession session);

  Future<void> clear();
}

/// The durable half of a login.
///
/// The access token is deliberately absent: it lasts fifteen minutes and
/// belongs in memory, while only the refresh token needs to survive a restart.
final class StoredSession {
  const StoredSession({
    required this.baseUrl,
    required this.refreshToken,
    required this.deviceLabel,
    required this.scope,
    required this.refreshExpiresAt,
  });

  final Uri baseUrl;
  final String refreshToken;
  final String deviceLabel;
  final String scope;
  final int refreshExpiresAt;

  Map<String, Object> _toJson() => {
    'base_url': baseUrl.toString(),
    'refresh_token': refreshToken,
    'device_label': deviceLabel,
    'scope': scope,
    'refresh_expires_at': refreshExpiresAt,
  };

  static StoredSession? _fromJson(Object? value) {
    if (value is! Map<String, dynamic>) return null;
    final baseUrl = value['base_url'];
    final refreshToken = value['refresh_token'];
    final deviceLabel = value['device_label'];
    final scope = value['scope'];
    final refreshExpiresAt = value['refresh_expires_at'];
    if (baseUrl is! String ||
        refreshToken is! String ||
        deviceLabel is! String ||
        scope is! String ||
        refreshExpiresAt is! int ||
        baseUrl.isEmpty ||
        refreshToken.isEmpty ||
        deviceLabel.isEmpty ||
        scope.isEmpty) {
      return null;
    }
    final parsedBaseUrl = Uri.tryParse(baseUrl);
    if (parsedBaseUrl == null ||
        !parsedBaseUrl.hasScheme ||
        parsedBaseUrl.host.isEmpty) {
      return null;
    }
    return StoredSession(
      baseUrl: parsedBaseUrl,
      refreshToken: refreshToken,
      deviceLabel: deviceLabel,
      scope: scope,
      refreshExpiresAt: refreshExpiresAt,
    );
  }

  @override
  bool operator ==(Object other) =>
      other is StoredSession &&
      baseUrl == other.baseUrl &&
      refreshToken == other.refreshToken &&
      deviceLabel == other.deviceLabel &&
      scope == other.scope &&
      refreshExpiresAt == other.refreshExpiresAt;

  @override
  int get hashCode =>
      Object.hash(baseUrl, refreshToken, deviceLabel, scope, refreshExpiresAt);
}

/// Keeps the credential behind Android's Keystore-backed encrypted
/// preferences, rather than merely hiding it in the application's directory.
final class SecureTokenStore implements TokenStore {
  SecureTokenStore([
    FlutterSecureStorage storage = const FlutterSecureStorage(
      aOptions: AndroidOptions(encryptedSharedPreferences: true),
    ),
  ]) : _storage = storage;

  static const _key = 'rackphone.session';
  final FlutterSecureStorage _storage;

  @override
  Future<StoredSession?> read() async {
    try {
      final raw = await _storage.read(key: _key);
      if (raw == null) return null;
      final session = StoredSession._fromJson(jsonDecode(raw));
      if (session != null) return session;
    } on FormatException {
      // Handled below with every other unusable record shape.
    } on PlatformException {
      // The Keystore refuses to decrypt after a restore onto another device or
      // a change of screen lock. Without this the read throws on every launch
      // and the application never gets past startup - the one outcome this
      // whole method exists to avoid.
    }

    // Corruption costs one new login, and nothing more than that.
    try {
      await clear();
    } on PlatformException {
      // Nothing further to try; a login will overwrite the record anyway.
    }
    return null;
  }

  @override
  Future<void> write(StoredSession session) =>
      _storage.write(key: _key, value: jsonEncode(session._toJson()));

  @override
  Future<void> clear() => _storage.delete(key: _key);
}

/// A device-free store for controller tests and widget harnesses.
final class InMemoryTokenStore implements TokenStore {
  InMemoryTokenStore([StoredSession? session]) : _session = session;

  StoredSession? _session;

  @override
  Future<StoredSession?> read() async => _session;

  @override
  Future<void> write(StoredSession session) async => _session = session;

  @override
  Future<void> clear() async => _session = null;
}
