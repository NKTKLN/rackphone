import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'errors.dart';
import 'models.dart';

typedef RefreshTokenProvider = Future<String?> Function();

/// Awaited, not fired and forgotten: the server rotates the refresh token as
/// it renews, so a caller that persists the new one must be given the chance to
/// finish before the old one is used again.
typedef TokensRenewed = FutureOr<void> Function(Tokens tokens);

/// The gateway operations are an interface so screens can be exercised
/// without a network, credentials, or a running rack underneath them.
abstract class GatewayApi {
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope,
  });

  Future<Tokens> refresh(String refreshToken);
  Future<void> logOut(String refreshToken);
  Future<List<RackUnit>> units();
  Future<GatewayStats> stats();
  Future<GatewayHealth> health();
  Future<List<GatewayEvent>> events({
    String? kind,
    String? unit,
    int? since,
    int? limit,
  });
  Future<List<GatewayEvent>> messages({String? unit, int? since, int? limit});
  Future<List<GatewayEvent>> calls({String? unit, int? since, int? limit});
  Stream<GatewayEvent> stream();
  void close();
}

/// The real gateway implementation, with only the access token kept in
/// memory; durable refresh-token storage remains the device layer's decision.
class GatewayClient implements GatewayApi {
  factory GatewayClient({
    required Uri baseUrl,
    http.Client? httpClient,
    RefreshTokenProvider? refreshTokenProvider,
    TokensRenewed? onTokensRenewed,
  }) => GatewayClient._(
    baseUrl,
    httpClient ?? http.Client(),
    httpClient == null,
    refreshTokenProvider,
    onTokensRenewed,
  );

  GatewayClient._(
    this._baseUrl,
    this._httpClient,
    this._ownsHttpClient,
    this._refreshTokenProvider,
    this._onTokensRenewed,
  );

  final Uri _baseUrl;
  final http.Client _httpClient;
  final bool _ownsHttpClient;
  final RefreshTokenProvider? _refreshTokenProvider;
  final TokensRenewed? _onTokensRenewed;
  String? _accessToken;
  Future<Tokens>? _renewal;

  @override
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) async {
    final response = await _postJson('/api/login', {
      'username': username,
      'password': password,
      'device_label': deviceLabel,
      'totp_code': totpCode,
      'recovery_code': recoveryCode,
      'scope': scope,
    });
    _throwForStatus(response);
    final tokens = Tokens.fromJson(_jsonObject(response.body));
    _accessToken = tokens.accessToken;
    return tokens;
  }

  @override
  Future<Tokens> refresh(String refreshToken) async {
    final response = await _postJson('/api/refresh', {
      'refresh_token': refreshToken,
    });
    _throwForStatus(response);
    final tokens = Tokens.fromJson(_jsonObject(response.body));
    _accessToken = tokens.accessToken;
    return tokens;
  }

  @override
  Future<void> logOut(String refreshToken) async {
    final response = await _postJson('/api/logout', {
      'refresh_token': refreshToken,
    });
    _throwForStatus(response);
    _accessToken = null;
  }

  @override
  Future<List<RackUnit>> units() async => _list(
    await _authenticated(() => http.Request('GET', _uri('/api/units'))),
    RackUnit.fromJson,
  );

  @override
  Future<GatewayStats> stats() async {
    final response = await _authenticated(
      () => http.Request('GET', _uri('/api/stats')),
    );
    return GatewayStats.fromJson(
      _jsonObject(await response.stream.bytesToString()),
    );
  }

  @override
  Future<GatewayHealth> health() async {
    final response = await _checked(
      await _send(http.Request('GET', _uri('/health'))),
    );
    return GatewayHealth.fromJson(
      _jsonObject(await response.stream.bytesToString()),
    );
  }

  @override
  Future<List<GatewayEvent>> events({
    String? kind,
    String? unit,
    int? since,
    int? limit,
  }) => _eventsAt(
    '/api/events',
    kind: kind,
    unit: unit,
    since: since,
    limit: limit,
  );

  @override
  Future<List<GatewayEvent>> messages({String? unit, int? since, int? limit}) =>
      _eventsAt('/api/messages', unit: unit, since: since, limit: limit);

  @override
  Future<List<GatewayEvent>> calls({String? unit, int? since, int? limit}) =>
      _eventsAt('/api/calls', unit: unit, since: since, limit: limit);

  @override
  Stream<GatewayEvent> stream() async* {
    final response = await _authenticated(
      () => http.Request('GET', _uri('/api/stream')),
    );
    final data = <String>[];
    // HTTP chunks have no relationship to SSE frames. UTF-8 decoding and line
    // splitting preserve partial characters and lines across chunk boundaries.
    await for (final line
        in response.stream
            .transform(utf8.decoder)
            .transform(const LineSplitter())) {
      if (line.isEmpty) {
        if (data.isNotEmpty) {
          yield GatewayEvent.fromJson(_jsonObject(data.join('\n')));
          data.clear();
        }
      } else if (line.startsWith('data:')) {
        data.add(line.substring(5).trimLeft());
      }
    }
    if (data.isNotEmpty) {
      yield GatewayEvent.fromJson(_jsonObject(data.join('\n')));
    }
  }

  @override
  void close() {
    if (_ownsHttpClient) _httpClient.close();
  }

  Future<List<GatewayEvent>> _eventsAt(
    String path, {
    String? kind,
    String? unit,
    int? since,
    int? limit,
  }) async {
    final query = <String, String>{
      'kind': ?kind,
      'unit': ?unit,
      if (since != null) 'since': '$since',
      if (limit != null) 'limit': '$limit',
    };
    final response = await _authenticated(
      () => http.Request('GET', _uri(path, query)),
    );
    return _list(response, GatewayEvent.fromJson);
  }

  Future<List<T>> _list<T>(
    http.StreamedResponse response,
    T Function(Map<String, dynamic>) parse,
  ) async {
    final decoded = _json(await response.stream.bytesToString());
    if (decoded is! List) {
      throw const GatewayProtocolException('expected a JSON list');
    }
    return decoded
        .map((item) => parse(_asObject(item)))
        .toList(growable: false);
  }

  Future<http.StreamedResponse> _authenticated(
    http.BaseRequest Function() requestFactory,
  ) async {
    var response = await _sendWithAccessToken(requestFactory());
    if (response.statusCode != 401) return _checked(response);
    await response.stream.drain<void>();

    final provider = _refreshTokenProvider;
    final refreshToken = provider == null ? null : await provider();
    if (refreshToken == null || refreshToken.isEmpty) {
      throw const GatewayAuthException('invalid or missing bearer token');
    }
    await _renewOnce(refreshToken);

    // One retry prevents an expired or revoked credential from turning a
    // normal request into an unbounded refresh loop.
    response = await _sendWithAccessToken(requestFactory());
    if (response.statusCode == 401) {
      final body = await response.stream.bytesToString();
      throw GatewayAuthException(_reason(body));
    }
    return _checked(response);
  }

  /// Renews the token pair once, however many callers asked at once.
  ///
  /// Several requests meet a 401 at the same moment - on resume the feed and
  /// the stats call go out together. The server rotates the refresh token as it
  /// renews, so a second concurrent renewal would present one that was revoked
  /// microseconds earlier and log the device out for no reason at all.
  Future<Tokens> _renewOnce(String refreshToken) {
    final running = _renewal;
    if (running != null) return running;
    final started = refresh(refreshToken).then((tokens) async {
      await _onTokensRenewed?.call(tokens);
      return tokens;
    });
    _renewal = started.whenComplete(() => _renewal = null);
    return _renewal!;
  }

  Future<http.StreamedResponse> _sendWithAccessToken(http.BaseRequest request) {
    request.headers['Authorization'] = 'Bearer ${_accessToken ?? ''}';
    return _send(request);
  }

  Future<http.StreamedResponse> _send(http.BaseRequest request) async {
    try {
      return await _httpClient.send(request);
    } on GatewayException {
      rethrow;
    } catch (error) {
      throw GatewayNetworkException(error);
    }
  }

  Future<http.StreamedResponse> _checked(http.StreamedResponse response) async {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final body = await response.stream.bytesToString();
      _throwStatus(response.statusCode, response.headers, body);
    }
    return response;
  }

  Future<http.Response> _postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    try {
      return await _httpClient.post(
        _uri(path),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode(body),
      );
    } catch (error) {
      throw GatewayNetworkException(error);
    }
  }

  Uri _uri(String path, [Map<String, String>? query]) =>
      _baseUrl.resolve(path).replace(queryParameters: query);

  void _throwForStatus(http.Response response) {
    if (response.statusCode >= 200 && response.statusCode < 300) return;
    _throwStatus(response.statusCode, response.headers, response.body);
  }

  /// Reads one response header without trusting its case.
  ///
  /// `dart:io` lowercases what it receives, so production only ever sees the
  /// lowercase form - but a hand-built [http.Response] in a test, or a
  /// different transport, keeps whatever case it was given, and a lookup that
  /// misses simply reports no delay at all rather than failing loudly.
  static String? _header(Map<String, String> headers, String name) {
    for (final entry in headers.entries) {
      if (entry.key.toLowerCase() == name) {
        return entry.value;
      }
    }
    return null;
  }

  Never _throwStatus(int status, Map<String, String> headers, String body) {
    final reason = _reason(body);
    switch (status) {
      case 401:
        throw GatewayAuthException(reason);
      case 403:
        throw GatewayForbiddenException(reason);
      case 423:
        final seconds =
            int.tryParse(_header(headers, 'retry-after') ?? '') ?? 0;
        throw GatewayLockedException(
          retryAfter: Duration(seconds: seconds < 0 ? 0 : seconds),
        );
      case 503:
        throw const GatewayUnavailableException();
      default:
        throw GatewayProtocolException('gateway returned HTTP $status');
    }
  }

  String _reason(String body) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map && decoded['detail'] is String) {
        return decoded['detail'] as String;
      }
    } on FormatException {
      // A proxy may replace an error body with HTML; the status still carries
      // enough meaning for the typed exception.
    }
    return '';
  }

  dynamic _json(String body) {
    try {
      return jsonDecode(body);
    } on FormatException catch (error) {
      throw GatewayProtocolException('invalid JSON: ${error.message}');
    }
  }

  Map<String, dynamic> _jsonObject(String body) => _asObject(_json(body));

  Map<String, dynamic> _asObject(dynamic value) {
    if (value is! Map) {
      throw const GatewayProtocolException('expected a JSON object');
    }
    return value.map((key, value) => MapEntry(key.toString(), value));
  }
}
