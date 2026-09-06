import 'package:flutter/foundation.dart';

import '../api/gateway_client.dart';
import '../api/models.dart';

/// Owns the file-transfer state for one rack unit.
final class FilesController extends ChangeNotifier {
  FilesController({
    required GatewayApi gateway,
    required this.unit,
    this.fileSizeLimit = maximumFileSize,
  }) : _gateway = gateway.fileTransfer;

  /// Shared with the gateway so an oversized body never consumes its disk.
  static const int maximumFileSize = 512 * 1024 * 1024;
  static const String _plainNameRefusal =
      'file name must be one plain, visible name';

  final GatewayFilesApi _gateway;
  final String unit;
  final int fileSizeLimit;
  List<UnitFile> _files = const [];
  bool _loading = false;
  Object? _failure;

  List<UnitFile> get files => _files;
  bool get loading => _loading;
  Object? get failure => _failure;

  /// Mirrors the gateway's rules only to answer sooner; the gateway remains
  /// the authority for every name it receives.
  String? nameRefusal(String name) {
    final refused =
        name.isEmpty ||
        name.startsWith('.') ||
        name.contains('..') ||
        name.contains('/') ||
        name.contains(r'\') ||
        name.contains('\u0000') ||
        name.runes.any((value) => value < 32 || value == 127);
    return refused ? _plainNameRefusal : null;
  }

  Future<void> refresh() async {
    _loading = true;
    _failure = null;
    notifyListeners();
    try {
      _files = List.unmodifiable(await _gateway.files(unit));
    } catch (failure) {
      _failure = failure;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  Future<void> upload(String name, Uint8List bytes) async {
    final refusal = nameRefusal(name);
    if (refusal != null) {
      _setFailure(refusal);
      return;
    }
    if (bytes.length > fileSizeLimit) {
      _setFailure('File exceeds the $fileSizeLimit-byte size limit.');
      return;
    }
    await _change(() => _gateway.uploadFile(unit, name, bytes));
  }

  Future<Uint8List?> download(String name) async {
    _failure = null;
    notifyListeners();
    try {
      return await _gateway.downloadFile(unit, name);
    } catch (failure) {
      _setFailure(failure);
      return null;
    }
  }

  Future<void> remove(String name) async {
    final refusal = nameRefusal(name);
    if (refusal != null) {
      _setFailure(refusal);
      return;
    }
    await _change(() => _gateway.removeFile(unit, name));
  }

  Future<void> _change(Future<void> Function() operation) async {
    _loading = true;
    _failure = null;
    notifyListeners();
    try {
      await operation();
      _files = List.unmodifiable(await _gateway.files(unit));
    } catch (failure) {
      _failure = failure;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  void _setFailure(Object failure) {
    _failure = failure;
    notifyListeners();
  }
}
