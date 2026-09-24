import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:rackphone_client/src/api/errors.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';

const _tokens = {
  'refresh_token': 'refresh-2',
  'access_token': 'access-2',
  'scope': 'control',
  'refresh_expires_at': 200,
  'access_expires_at': 100,
};

void main() {
  test('login sends the server shape and keeps the access token', () async {
    late Map<String, dynamic> body;
    final fake = MockClient((request) async {
      body = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(jsonEncode(_tokens), 200);
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );

    final result = await client.logIn(
      username: 'admin',
      password: 'secret',
      deviceLabel: 'operator phone',
      totpCode: '123456',
    );

    expect(body['device_label'], 'operator phone');
    expect(body['totp_code'], '123456');
    expect(body['scope'], 'control');
    expect(result.accessToken, 'access-2');
  });

  group('login status mapping', () {
    test('401 carries its authentication reason', () async {
      final client = _loginClient(401, {'detail': 'bad_credentials'});
      await expectLater(
        _login(client),
        throwsA(
          isA<GatewayAuthException>().having(
            (error) => error.reason,
            'reason',
            'bad_credentials',
          ),
        ),
      );
    });

    for (final reason in ['totp_required', 'bad_totp']) {
      test('403 carries $reason', () async {
        final client = _loginClient(403, {'detail': reason});
        await expectLater(
          _login(client),
          throwsA(
            isA<GatewayForbiddenException>().having(
              (error) => error.reason,
              'reason',
              reason,
            ),
          ),
        );
      });
    }

    test('423 reads Retry-After as seconds', () async {
      final client = GatewayClient(
        baseUrl: Uri.parse('https://gateway.example'),
        httpClient: MockClient(
          (_) async => http.Response(
            jsonEncode({'detail': 'locked'}),
            423,
            // dart:io lowercases response headers, so this is the shape
            // production actually sees; the lookup tolerates either.
            headers: {'retry-after': '61'},
          ),
        ),
      );
      await expectLater(
        _login(client),
        throwsA(
          isA<GatewayLockedException>().having(
            (error) => error.retryAfter,
            'retryAfter',
            const Duration(seconds: 61),
          ),
        ),
      );
    });

    test('503 becomes unavailable', () async {
      await expectLater(
        _login(_loginClient(503, const {})),
        throwsA(isA<GatewayUnavailableException>()),
      );
    });
  });

  test('two calls meeting a 401 together renew the token only once', () async {
    // The server rotates the refresh token as it renews, so a second
    // concurrent renewal would present a token revoked microseconds earlier
    // and log the device out for no reason.
    var refreshCalls = 0;
    var dataCalls = 0;
    final fake = MockClient((request) async {
      if (request.url.path == '/api/refresh') {
        refreshCalls++;
        // A turn of the event loop, so a second caller has every chance to
        // start a renewal of its own.
        await Future<void>.delayed(Duration.zero);
        return http.Response(jsonEncode(_tokens), 200);
      }
      dataCalls++;
      return dataCalls <= 2
          ? http.Response('', 401)
          : http.Response(jsonEncode(const <Object>[]), 200);
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
      refreshTokenProvider: () async => 'refresh-1',
    );

    final results = await Future.wait([client.units(), client.units()]);

    expect(results, everyElement(isEmpty));
    expect(refreshCalls, 1);
    client.close();
  });

  test(
    'a data 401 refreshes once and retries with the new access token',
    () async {
      var unitsCalls = 0;
      var refreshCalls = 0;
      var renewedCalls = 0;
      final fake = MockClient((request) async {
        if (request.url.path == '/api/login') {
          return http.Response(
            jsonEncode({..._tokens, 'access_token': 'old'}),
            200,
          );
        }
        if (request.url.path == '/api/refresh') {
          refreshCalls++;
          expect(jsonDecode(request.body), {'refresh_token': 'refresh-1'});
          expect(request.headers['authorization'], isNull);
          return http.Response(jsonEncode(_tokens), 200);
        }
        unitsCalls++;
        expect(
          request.headers['authorization'],
          'Bearer ${unitsCalls == 1 ? 'old' : 'access-2'}',
        );
        return unitsCalls == 1
            ? http.Response('', 401)
            : http.Response(
                jsonEncode([
                  {
                    'name': 'lisa01',
                    'label': 'Lisa',
                    'capabilities': ['sms'],
                  },
                ]),
                200,
              );
      });
      final client = GatewayClient(
        baseUrl: Uri.parse('https://gateway.example'),
        httpClient: fake,
        refreshTokenProvider: () async => 'refresh-1',
        onTokensRenewed: (_) => renewedCalls++,
      );
      await _login(client);

      final units = await client.units();

      expect(units.single.name, 'lisa01');
      expect(unitsCalls, 2);
      expect(refreshCalls, 1);
      expect(renewedCalls, 1);
    },
  );

  test('a second data 401 throws without refreshing again', () async {
    var refreshCalls = 0;
    final fake = MockClient((request) async {
      if (request.url.path == '/api/login') {
        return http.Response(jsonEncode(_tokens), 200);
      }
      if (request.url.path == '/api/refresh') {
        refreshCalls++;
        return http.Response(jsonEncode(_tokens), 200);
      }
      return http.Response('', 401);
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
      refreshTokenProvider: () async => 'refresh-1',
    );
    await _login(client);

    await expectLater(client.units(), throwsA(isA<GatewayAuthException>()));
    expect(refreshCalls, 1);
  });

  test('SSE frame split across chunks arrives as one event', () async {
    final frame = utf8.encode(
      'data: {"id":9,"unit":"lisa01","kind":"sms","body":"hello"}\n\n',
    );
    final fake = _ChunkedClient([frame.sublist(0, 17), frame.sublist(17)]);
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );

    final events = await client.stream().toList();

    expect(events.single.id, 9);
    expect(events.single.body, 'hello');
  });

  test('telemetry reads its unit route and parses samples', () async {
    final fake = MockClient((request) async {
      expect(request.url.path, '/api/units/lisa%2001/telemetry');
      return http.Response(
        jsonEncode({
          'unit': 'lisa 01',
          'up': true,
          'collected_at': 10,
          'samples': {'rackphone_uptime_seconds': 12.5},
        }),
        200,
      );
    });
    final GatewayApi client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );

    final telemetry = await client.telemetry('lisa 01');

    expect(telemetry.unit, 'lisa 01');
    expect(telemetry.uptime, 12.5);
  });

  test(
    'call controls use authenticated unit routes and parse acceptance',
    () async {
      final paths = <String>[];
      final fake = MockClient((request) async {
        if (request.url.path == '/api/login') {
          return http.Response(jsonEncode(_tokens), 200);
        }
        expect(request.method, 'POST');
        expect(request.headers['authorization'], 'Bearer access-2');
        paths.add(request.url.path);
        final status = request.url.path.endsWith('/answer')
            ? 'answered'
            : 'rejected';
        return http.Response(
          jsonEncode({'status': status, 'accepted': true}),
          200,
        );
      });
      final client = GatewayClient(
        baseUrl: Uri.parse('https://gateway.example/root/'),
        httpClient: fake,
      );
      await _login(client);

      final answered = await client.answerCall('lisa 01');
      final rejected = await client.rejectCall('lisa 01');
      await client.endCall('lisa 01');

      expect(answered.status, 'answered');
      expect(rejected.status, 'rejected');
      expect(answered.accepted, isTrue);
      expect(paths, <String>[
        '/api/units/lisa%2001/call/answer',
        '/api/units/lisa%2001/call/reject',
        '/api/units/lisa%2001/call/end',
      ]);
      expect(
        client.callAudioUri('lisa 01').toString(),
        'wss://gateway.example/api/units/lisa%2001/call/audio',
      );
    },
  );

  test('contacts read the unit route, and refresh only when asked', () async {
    final urls = <Uri>[];
    final fake = MockClient((request) async {
      if (request.url.path == '/api/login') {
        return http.Response(jsonEncode(_tokens), 200);
      }
      urls.add(request.url);
      return http.Response(
        jsonEncode([
          {'name': 'Andrew', 'number': '+7 900', 'normalized': '+7900'},
        ]),
        200,
      );
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );
    await _login(client);

    final contacts = await client.contacts('lisa01');
    await client.contacts('lisa01', refresh: true);

    expect(contacts.single.name, 'Andrew');
    expect(contacts.single.address, '+7900');
    expect(urls.first.path, '/api/units/lisa01/contacts');
    expect(urls.first.hasQuery, isFalse);
    expect(urls.last.queryParameters, {'refresh': 'true'});
  });

  test('administration uses its routes and methods', () async {
    final seen = <String>[];
    final fake = MockClient((request) async {
      if (request.url.path == '/api/login') {
        return http.Response(jsonEncode(_tokens), 200);
      }
      seen.add('${request.method} ${request.url.path}');
      return switch (request.url.path) {
        '/api/sessions' => http.Response(
          jsonEncode([
            {'id': 4, 'device_label': 'Pixel', 'scope': 'admin'},
          ]),
          200,
        ),
        '/api/audit' => http.Response(
          jsonEncode([
            {'id': 1, 'at': 5, 'action': 'send_sms', 'subject': 'lisa01'},
          ]),
          200,
        ),
        '/api/totp' when request.method == 'POST' => http.Response(
          jsonEncode({
            'secret': 'S',
            'recovery_codes': ['r1'],
          }),
          200,
        ),
        _ => http.Response('', 204),
      };
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );
    await _login(client);

    expect((await client.sessions()).single.deviceLabel, 'Pixel');
    await client.revokeSession(4);
    await client.revokeAllSessions();
    expect((await client.audit(limit: 10)).single.action, 'send_sms');
    expect((await client.enableTotp('pw')).recoveryCodes, ['r1']);
    await client.disableTotp('pw');

    expect(seen, [
      'GET /api/sessions',
      'DELETE /api/sessions/4',
      'POST /api/sessions/revoke-all',
      'GET /api/audit',
      'POST /api/totp',
      'DELETE /api/totp',
    ]);
  });

  test('a unit socket is opened with a token from a live session', () async {
    // The other isolate may have rotated the refresh token, revoking the
    // session this isolate's access token names; the socket must not use it.
    var refreshes = 0;
    final fake = MockClient((request) async {
      if (request.url.path == '/api/refresh') refreshes++;
      return http.Response(jsonEncode(_tokens), 200);
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('http://127.0.0.1:9'),
      httpClient: fake,
      refreshTokenProvider: () async => 'refresh-1',
    );

    await expectLater(client.callAudio('lisa01'), throwsA(anything));
    expect(refreshes, 1);
  });

  test('dialling and keys carry their JSON bodies', () async {
    final bodies = <String, Object?>{};
    final fake = MockClient((request) async {
      if (request.url.path == '/api/login') {
        return http.Response(jsonEncode(_tokens), 200);
      }
      bodies[request.url.pathSegments.last] = jsonDecode(request.body);
      return http.Response(jsonEncode({'status': 'ok', 'accepted': true}), 200);
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );
    await _login(client);

    await client.dial('lisa01', '+7900');
    await client.sendDtmf('lisa01', '1#');

    expect(bodies, {
      'dial': {'to': '+7900'},
      'dtmf': {'digits': '1#'},
    });
  });

  test('sending posts the message and returns the stored copy', () async {
    late Map<String, dynamic> sent;
    final fake = MockClient((request) async {
      if (request.url.path == '/api/login') {
        return http.Response(jsonEncode(_tokens), 200);
      }
      expect(request.method, 'POST');
      expect(request.url.path, '/api/messages');
      expect(request.headers['authorization'], 'Bearer access-2');
      sent = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(
        jsonEncode({
          'accepted': true,
          'event': {
            'id': 9,
            'unit': 'lisa01',
            'kind': 'sms',
            'address': '+7900',
            'body': 'on my way',
            'ts': 1700000000000,
            'direction': 'out',
            'raw_json': '{"kind": "sms", "direction": "out"}',
          },
        }),
        200,
      );
    });
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );
    await _login(client);

    final event = await client.sendMessage('lisa01', '+7900', 'on my way');

    expect(sent, {'unit': 'lisa01', 'to': '+7900', 'body': 'on my way'});
    expect(event.id, 9);
    expect(event.direction, 'out');
    expect(event.body, 'on my way');
  });

  for (final status in [403, 404]) {
    test('telemetry HTTP $status remains a typed gateway failure', () async {
      final GatewayApi client = GatewayClient(
        baseUrl: Uri.parse('https://gateway.example'),
        httpClient: MockClient((_) async => http.Response('{}', status)),
      );

      await expectLater(
        client.telemetry('lisa01'),
        throwsA(isA<GatewayException>()),
      );
    });
  }

  test('close leaves an injected client open', () async {
    final fake = _TrackingClient(
      MockClient((_) async => http.Response('{}', 200)),
    );
    final client = GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: fake,
    );

    client.close();

    expect(fake.closed, isFalse);
    expect(
      (await fake.get(Uri.parse('https://gateway.example/health'))).statusCode,
      200,
    );
  });
}

GatewayClient _loginClient(int status, Map<String, dynamic> body) =>
    GatewayClient(
      baseUrl: Uri.parse('https://gateway.example'),
      httpClient: MockClient(
        (_) async => http.Response(jsonEncode(body), status),
      ),
    );

Future<void> _login(GatewayClient client) async {
  await client.logIn(
    username: 'admin',
    password: 'secret',
    deviceLabel: 'test',
  );
}

final class _ChunkedClient extends http.BaseClient {
  _ChunkedClient(this.chunks);

  final List<List<int>> chunks;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async =>
      http.StreamedResponse(Stream<List<int>>.fromIterable(chunks), 200);
}

final class _TrackingClient extends http.BaseClient {
  _TrackingClient(this.delegate);

  final MockClient delegate;
  bool closed = false;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      delegate.send(request);

  @override
  void close() {
    closed = true;
    delegate.close();
  }
}
