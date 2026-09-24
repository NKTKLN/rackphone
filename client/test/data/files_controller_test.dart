import 'dart:async';
import 'dart:typed_data';

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/api/gateway_client.dart';
import 'package:rackphone_client/src/api/models.dart';
import 'package:rackphone_client/src/data/files_controller.dart';

void main() {
  test('listing populates and clears its loading flag', () async {
    final gateway = _FilesGateway()
      ..listed = const [UnitFile(name: 'map.zip', size: 12, modifiedAt: 7)];
    final controller = FilesController(gateway: gateway, unit: 'lisa01');
    final loading = <bool>[];
    controller.addListener(() => loading.add(controller.loading));

    await controller.refresh();

    expect(loading, containsAllInOrder([true, false]));
    expect(controller.files.single.name, 'map.zip');
  });

  test('a listing failure lands in state', () async {
    final failure = StateError('offline');
    final gateway = _FilesGateway()..failure = failure;
    final controller = FilesController(gateway: gateway, unit: 'lisa01');

    await controller.refresh();

    expect(controller.failure, same(failure));
    expect(controller.loading, isFalse);
  });

  test('every gateway-refused name receives the same verdict', () {
    final controller = FilesController(
      gateway: _FilesGateway(),
      unit: 'lisa01',
    );
    const verdict = 'file name must be one plain, visible name';
    // The shapes come from the fixture the gateway's own suite reads, so this
    // mirror cannot fall behind the rule it mirrors without a test noticing.
    final cases =
        jsonDecode(
              File(
                '../tests/fixtures/refused_file_names.json',
              ).readAsStringSync(),
            )
            as Map<String, dynamic>;

    for (final name in (cases['refused'] as List).cast<String>()) {
      expect(controller.nameRefusal(name), verdict, reason: name);
    }
    for (final name in (cases['accepted'] as List).cast<String>()) {
      expect(controller.nameRefusal(name), isNull, reason: name);
    }
  });

  test('an oversized upload is refused before the gateway call', () async {
    final gateway = _FilesGateway();
    final controller = FilesController(
      gateway: gateway,
      unit: 'lisa01',
      fileSizeLimit: 3,
    );
    final oversized = Uint8List(4);

    await controller.upload('large.bin', oversized);

    expect(gateway.uploads, 0);
    expect(controller.failure.toString(), contains('3-byte size limit'));
  });
}

final class _FilesGateway implements GatewayApi, GatewayFilesApi {
  List<UnitFile> listed = const [];
  Object? failure;
  int uploads = 0;

  @override
  Future<List<UnitFile>> files(String unit) async {
    final problem = failure;
    if (problem != null) throw problem;
    return listed;
  }

  @override
  Future<void> uploadFile(String unit, String name, Uint8List bytes) async {
    uploads++;
  }

  @override
  Future<Uint8List> downloadFile(String unit, String name) async =>
      Uint8List(0);
  @override
  Future<void> removeFile(String unit, String name) async {}
  @override
  Future<List<GatewayEvent>> calls({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();
  @override
  void close() {}
  @override
  Future<List<GatewayEvent>> events({
    String? kind,
    String? unit,
    int? since,
    int? limit,
  }) => throw UnimplementedError();
  @override
  Future<GatewayHealth> health() => throw UnimplementedError();
  @override
  Future<Tokens> logIn({
    required String username,
    required String password,
    required String deviceLabel,
    String? totpCode,
    String? recoveryCode,
    String scope = 'control',
  }) => throw UnimplementedError();
  @override
  Future<void> logOut(String refreshToken) => throw UnimplementedError();
  @override
  Future<List<GatewayEvent>> messages({String? unit, int? since, int? limit}) =>
      throw UnimplementedError();
  @override
  Future<Tokens> refresh(String refreshToken) => throw UnimplementedError();
  @override
  Future<GatewayStats> stats() => throw UnimplementedError();
  @override
  Stream<GatewayEvent> stream() => const Stream.empty();
  @override
  Future<UnitTelemetry> telemetry(String unit) => throw UnimplementedError();
  @override
  Future<List<RackUnit>> units() => throw UnimplementedError();
}
