import 'dart:convert';

import 'package:flutter/services.dart';

import 'models.dart';

/// What the screen is allowed to ask the unit to do.
///
/// An interface rather than a bare `MethodChannel` so the widgets can be
/// exercised without an Android engine underneath them: every one of these
/// calls ends in the radio or in SharedPreferences, and neither exists in a
/// test.
abstract class CompanionControl {
  Future<CompanionStatus> status();

  Future<List<SendRecord>> recent({int limit});

  Future<String> token();

  Future<CompanionStatus> setConfig({
    bool? keepaliveEnabled,
    String? keepaliveTo,
    int? keepaliveIntervalHours,
    String? keepaliveBody,
    String? keepaliveSubs,
    bool? collectSms,
    bool? collectCalls,
    bool? includeBody,
    int? subId,
  });

  Future<SendRecord> send({required String to, required String body});

  Future<SendRecord?> keepaliveNow();

  /// Ask every SIM its balance over USSD. Slow: the operator answers in
  /// seconds, not instantly.
  Future<List<BalanceReading>> checkBalance();

  Future<String> rotateToken();

  Future<void> requestPermissions();
}

/// The real implementation, over the method channel to `MainActivity`.
///
/// Native returns JSON strings rather than platform maps: `status.json` is
/// already the format the host reads, and re-encoding it as a nested
/// `Map<Object?, Object?>` only to decode it again would give the UI a second
/// definition of the same document to drift from.
class MethodChannelControl implements CompanionControl {
  const MethodChannelControl([
    this._channel = const MethodChannel(
      'com.nktkln.rackphone.companion/control',
    ),
  ]);

  final MethodChannel _channel;

  @override
  Future<CompanionStatus> status() async =>
      CompanionStatus.fromJson(await _json('status'));

  @override
  Future<List<SendRecord>> recent({int limit = 20}) async {
    final raw = await _channel.invokeMethod<String>('recent', {'limit': limit});
    final decoded = jsonDecode(raw ?? '[]') as List<dynamic>;
    return decoded
        .map(
          (dynamic e) =>
              SendRecord.fromJson(Map<String, dynamic>.from(e as Map)),
        )
        .toList(growable: false);
  }

  @override
  Future<String> token() async =>
      await _channel.invokeMethod<String>('token') ?? '';

  @override
  Future<CompanionStatus> setConfig({
    bool? keepaliveEnabled,
    String? keepaliveTo,
    int? keepaliveIntervalHours,
    String? keepaliveBody,
    String? keepaliveSubs,
    bool? collectSms,
    bool? collectCalls,
    bool? includeBody,
    int? subId,
  }) async {
    // Only the keys that were passed: native treats the call as a patch, the
    // same way the CONFIG broadcast does.
    final args = <String, dynamic>{
      'keepalive_enabled': ?keepaliveEnabled,
      'keepalive_to': ?keepaliveTo,
      'keepalive_interval_hours': ?keepaliveIntervalHours,
      'keepalive_body': ?keepaliveBody,
      'keepalive_subs': ?keepaliveSubs,
      'collect_sms': ?collectSms,
      'collect_calls': ?collectCalls,
      'include_body': ?includeBody,
      'sub_id': ?subId,
    };
    return CompanionStatus.fromJson(await _json('setConfig', args));
  }

  @override
  Future<SendRecord> send({required String to, required String body}) async =>
      SendRecord.fromJson(await _json('send', {'to': to, 'body': body}));

  @override
  Future<SendRecord?> keepaliveNow() async {
    final record = await _json('keepaliveNow');
    if (record['status'] == 'not_due') return null;
    return SendRecord.fromJson(record);
  }

  @override
  Future<List<BalanceReading>> checkBalance() async {
    final raw = await _channel.invokeMethod<String>('checkBalance');
    final decoded = jsonDecode(raw ?? '[]') as List<dynamic>;
    return decoded
        .map(
          (dynamic e) =>
              BalanceReading.fromJson(Map<String, dynamic>.from(e as Map)),
        )
        .toList(growable: false);
  }

  @override
  Future<String> rotateToken() async =>
      await _channel.invokeMethod<String>('rotateToken') ?? '';

  @override
  Future<void> requestPermissions() =>
      _channel.invokeMethod<void>('requestPermissions');

  Future<Map<String, dynamic>> _json(
    String method, [
    Map<String, dynamic>? args,
  ]) async {
    final raw = await _channel.invokeMethod<String>(method, args);
    return Map<String, dynamic>.from(jsonDecode(raw ?? '{}') as Map);
  }
}
