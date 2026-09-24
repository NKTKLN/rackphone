import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'errors.dart';
import 'models.dart';
import '../call/call_socket.dart';
import '../screen/screen_socket.dart';

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
  Future<UnitTelemetry> telemetry(String unit);
  Stream<GatewayEvent> stream();
  void close();
}

/// File operations are separate so existing lightweight gateway fakes do not
/// have to pretend to provide a byte-transfer boundary they never exercise.
abstract interface class GatewayFilesApi {
  Future<List<UnitFile>> files(String unit);
  Future<void> uploadFile(String unit, String name, Uint8List bytes);
  Future<Uint8List> downloadFile(String unit, String name);
  Future<void> removeFile(String unit, String name);
}

extension GatewayFileAccess on GatewayApi {
  GatewayFilesApi get fileTransfer {
    final gateway = this;
    if (gateway is GatewayFilesApi) return gateway as GatewayFilesApi;
    throw UnsupportedError('This gateway does not provide file transfer.');
  }
}

/// Screen transport access kept as an extension so lightweight [GatewayApi]
/// test implementations do not acquire protocol or socket responsibilities.
extension GatewayScreenApi on GatewayApi {
  Future<ScreenSocket> screen(String unit) {
    final gateway = this;
    if (gateway is GatewayClient) return gateway.screen(unit);
    throw UnsupportedError('This gateway does not provide screen transport.');
  }
}

/// Sending stays separate so lightweight gateway fakes that only read do not
/// have to pretend to send.
abstract interface class GatewayMessagingApi {
  /// Sends one SMS and returns it as the gateway stored it.
  Future<GatewayEvent> sendMessage(String unit, String to, String body);
}

extension GatewayMessagingAccess on GatewayApi {
  GatewayMessagingApi? get messaging {
    final gateway = this;
    return gateway is GatewayMessagingApi
        ? gateway as GatewayMessagingApi
        : null;
  }
}

/// The address book stays separate for the same reason as sending.
abstract interface class GatewayContactsApi {
  /// A unit's contacts; [refresh] reads the unit rather than a recent copy.
  Future<List<Contact>> contacts(String unit, {bool refresh = false});
}

extension GatewayContactsAccess on GatewayApi {
  GatewayContactsApi? get addressBook {
    final gateway = this;
    return gateway is GatewayContactsApi ? gateway as GatewayContactsApi : null;
  }
}

/// Administration needs the `admin` scope, which a device holds only when
/// its operator asked for it at sign-in.
abstract interface class GatewayAdminApi {
  Future<List<Session>> sessions();
  Future<void> revokeSession(int id);

  /// Revokes every session, this device's included.
  Future<void> revokeAllSessions();
  Future<List<AuditEntry>> audit({int? limit});
  Future<TotpEnrollment> enableTotp(String password);
  Future<void> disableTotp(String password);
}

extension GatewayAdminAccess on GatewayApi {
  GatewayAdminApi? get admin {
    final gateway = this;
    return gateway is GatewayAdminApi ? gateway as GatewayAdminApi : null;
  }
}

/// Call controls stay separate so existing lightweight gateway fakes do not
/// acquire audio transport responsibilities they never exercise.
abstract interface class GatewayCallsApi {
  Future<CallActionResult> answerCall(String unit);
  Future<CallActionResult> rejectCall(String unit);

  /// Places a call from [unit] to [to].
  Future<CallActionResult> dial(String unit, String to);

  /// Ends whatever call [unit] has, ringing, dialling or connected.
  Future<CallActionResult> endCall(String unit);

  /// Presses keypad keys on [unit]'s connected call.
  Future<CallActionResult> sendDtmf(String unit, String digits);
  Future<CallAudioSocket> callAudio(String unit);
}

extension GatewayCallAccess on GatewayApi {
  GatewayCallsApi get callControl {
    final gateway = this;
    if (gateway is GatewayCallsApi) return gateway as GatewayCallsApi;
    throw UnsupportedError('This gateway does not provide call control.');
  }
}

/// The real gateway implementation, with only the access token kept in
/// memory; durable refresh-token storage remains the device layer's decision.
class GatewayClient
    implements
        GatewayApi,
        GatewayFilesApi,
        GatewayCallsApi,
        GatewayMessagingApi,
        GatewayContactsApi,
        GatewayAdminApi {
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
  Future<UnitTelemetry> telemetry(String unit) async {
    // The unit name lands in a path segment, so it is encoded rather than
    // interpolated: a label carrying a space or a slash would otherwise change
    // which route is being asked for.
    final response = await _authenticated(
      () => http.Request(
        'GET',
        _uri('/api/units/${Uri.encodeComponent(unit)}/telemetry'),
      ),
    );
    return UnitTelemetry.fromJson(
      _jsonObject(await response.stream.bytesToString()),
    );
  }

  String _filesPath(String unit, [String? name]) {
    final root = '/api/units/${Uri.encodeComponent(unit)}/files';
    return name == null ? root : '$root/${Uri.encodeComponent(name)}';
  }

  @override
  Future<List<UnitFile>> files(String unit) async => _list(
    await _authenticated(() => http.Request('GET', _uri(_filesPath(unit)))),
    UnitFile.fromJson,
  );

  @override
  Future<void> uploadFile(String unit, String name, Uint8List bytes) async {
    final query = <String, String>{'name': name};
    final response = await _authenticated(() {
      final request = http.Request('POST', _uri(_filesPath(unit), query));
      request.headers['Content-Type'] = 'application/octet-stream';
      request.bodyBytes = bytes;
      return request;
    });
    await response.stream.drain<void>();
  }

  @override
  Future<Uint8List> downloadFile(String unit, String name) async {
    final response = await _authenticated(
      () => http.Request('GET', _uri(_filesPath(unit, name))),
    );
    return response.stream.toBytes();
  }

  @override
  Future<void> removeFile(String unit, String name) async {
    final response = await _authenticated(
      () => http.Request('DELETE', _uri(_filesPath(unit, name))),
    );
    await response.stream.drain<void>();
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

  /// Opens the unit's authenticated channel-framed screen transport.
  Future<ScreenSocket> screen(String unit) async {
    final httpScheme = _baseUrl.scheme.toLowerCase();
    final uri = _uri(
      '/api/units/${Uri.encodeComponent(unit)}/screen',
    ).replace(scheme: httpScheme == 'http' ? 'ws' : 'wss');
    return ScreenSocket.connect(
      uri: uri,
      accessToken: await _liveAccessToken(),
    );
  }

  /// An access token whose refresh session is still the live one.
  ///
  /// A socket that holds the unit - its screen, a call's audio - is checked
  /// against the live refresh session, not just the token's signature. This
  /// app has two isolates that each rotate the one refresh token, and each
  /// rotation revokes the session the other isolate's access token names, so
  /// a token that still passes every HTTP route can be refused here. Renewing
  /// first costs one request on something that happens once per session.
  Future<String> _liveAccessToken() async {
    final provider = _refreshTokenProvider;
    final refreshToken = provider == null ? null : await provider();
    if (refreshToken != null && refreshToken.isNotEmpty) {
      await _renewOnce(refreshToken);
    }
    return _accessToken ?? '';
  }

  @override
  Future<CallActionResult> answerCall(String unit) =>
      _callAction(unit, 'answer');

  @override
  Future<CallActionResult> rejectCall(String unit) =>
      _callAction(unit, 'reject');

  @override
  Future<List<Session>> sessions() async {
    final response = await _authenticated(
      () => http.Request('GET', _uri('/api/sessions')),
    );
    return _objects(
      _json(await response.stream.bytesToString()),
    ).map(Session.fromJson).toList(growable: false);
  }

  @override
  Future<void> revokeSession(int id) async {
    final response = await _authenticated(
      () => http.Request('DELETE', _uri('/api/sessions/$id')),
    );
    await response.stream.drain<void>();
  }

  @override
  Future<void> revokeAllSessions() async {
    final response = await _authenticated(
      () => http.Request('POST', _uri('/api/sessions/revoke-all')),
    );
    await response.stream.drain<void>();
  }

  @override
  Future<List<AuditEntry>> audit({int? limit}) async {
    final response = await _authenticated(
      () => http.Request(
        'GET',
        _uri('/api/audit', limit == null ? null : {'limit': '$limit'}),
      ),
    );
    return _objects(
      _json(await response.stream.bytesToString()),
    ).map(AuditEntry.fromJson).toList(growable: false);
  }

  @override
  Future<TotpEnrollment> enableTotp(String password) async {
    final response = await _authenticated(
      () => _jsonRequest('POST', '/api/totp', {'password': password}),
    );
    return TotpEnrollment.fromJson(
      _jsonObject(await response.stream.bytesToString()),
    );
  }

  @override
  Future<void> disableTotp(String password) async {
    final response = await _authenticated(
      () => _jsonRequest('DELETE', '/api/totp', {'password': password}),
    );
    await response.stream.drain<void>();
  }

  http.Request _jsonRequest(
    String method,
    String path,
    Map<String, String> body,
  ) => http.Request(method, _uri(path))
    ..headers['Content-Type'] = 'application/json'
    ..body = jsonEncode(body);

  static Iterable<Map<String, dynamic>> _objects(dynamic decoded) {
    if (decoded is! List) {
      throw const GatewayProtocolException('expected a list');
    }
    return decoded.whereType<Map<String, dynamic>>();
  }

  @override
  Future<List<Contact>> contacts(String unit, {bool refresh = false}) async {
    final response = await _authenticated(
      () => http.Request(
        'GET',
        _uri(
          '/api/units/${Uri.encodeComponent(unit)}/contacts',
          refresh ? const {'refresh': 'true'} : null,
        ),
      ),
    );
    final decoded = _json(await response.stream.bytesToString());
    if (decoded is! List) {
      throw const GatewayProtocolException('contacts were not a list');
    }
    return decoded
        .whereType<Map<String, dynamic>>()
        .map(Contact.fromJson)
        .toList(growable: false);
  }

  @override
  Future<GatewayEvent> sendMessage(String unit, String to, String body) async {
    final response = await _authenticated(
      () => http.Request('POST', _uri('/api/messages'))
        ..headers['Content-Type'] = 'application/json'
        ..body = jsonEncode(<String, String>{
          'unit': unit,
          'to': to,
          'body': body,
        }),
    );
    final answer = _jsonObject(await response.stream.bytesToString());
    return GatewayEvent.fromJson(_asObject(answer['event']));
  }

  @override
  Future<CallActionResult> dial(String unit, String to) =>
      _callAction(unit, 'dial', {'to': to});

  @override
  Future<CallActionResult> endCall(String unit) => _callAction(unit, 'end');

  @override
  Future<CallActionResult> sendDtmf(String unit, String digits) =>
      _callAction(unit, 'dtmf', {'digits': digits});

  Future<CallActionResult> _callAction(
    String unit,
    String action, [
    Map<String, String>? body,
  ]) async {
    final response = await _authenticated(() {
      final request = http.Request(
        'POST',
        _uri('/api/units/${Uri.encodeComponent(unit)}/call/$action'),
      );
      if (body != null) {
        request
          ..headers['Content-Type'] = 'application/json'
          ..body = jsonEncode(body);
      }
      return request;
    });
    return CallActionResult.fromJson(
      _jsonObject(await response.stream.bytesToString()),
    );
  }

  /// Builds the audio endpoint without exposing or duplicating URL rules.
  Uri callAudioUri(String unit) {
    final httpScheme = _baseUrl.scheme.toLowerCase();
    return _uri(
      '/api/units/${Uri.encodeComponent(unit)}/call/audio',
    ).replace(scheme: httpScheme == 'http' ? 'ws' : 'wss');
  }

  @override
  Future<CallAudioSocket> callAudio(String unit) async =>
      CallAudioSocket.connect(
        uri: callAudioUri(unit),
        accessToken: await _liveAccessToken(),
      );

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
      case 404:
        throw const GatewayUnavailableException();
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
