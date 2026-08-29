package com.nktkln.rackphone.companion

import android.Manifest
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.util.UUID

/**
 * The setup screen, and the only place a person is involved.
 *
 * The UI is a convenience for the ten minutes a unit spends on a desk before it
 * goes in the rack: grant the permission, pick the SIM, prove a message
 * actually leaves. Everything it does is also reachable as a broadcast, because
 * once the unit is racked there is nobody to tap anything.
 *
 * The native side owns the state. Dart reads and writes it over this channel
 * rather than keeping its own copy, so a setting changed from the host and a
 * setting changed on screen are the same setting.
 */
class MainActivity : FlutterActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val config = Config.of(this)
        // First launch is also the setup: without a token nothing can drive the
        // app, and the host reads the token file over adb.
        if (!config.hasToken) config.rotateToken()
        HostFiles.publishToken(this, config.token)
        Keepalive.schedule(this)
        Balance.schedule(this)
        HostFiles.writeStatus(this)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result -> dispatch(call, result) }
    }

    private fun dispatch(call: MethodCall, result: MethodChannel.Result) {
        val config = Config.of(this)
        when (call.method) {
            "status" -> result.success(HostFiles.writeStatus(this).toString())

            "token" -> result.success(config.token)

            "recent" -> {
                val limit = call.argument<Int>("limit") ?: 20
                result.success(HostFiles.recent(this, limit).toString())
            }

            "setConfig" -> {
                call.argument<Boolean>("keepalive_enabled")?.let { config.keepaliveEnabled = it }
                call.argument<String>("keepalive_to")?.let { config.keepaliveTo = it.trim() }
                call.argument<Int>("keepalive_interval_hours")?.let {
                    config.keepaliveIntervalHours = it
                }
                call.argument<String>("keepalive_body")?.let { config.keepaliveBody = it }
                call.argument<String>("keepalive_subs")?.let { config.keepaliveSubs = it }
                call.argument<Boolean>("collect_sms")?.let { config.collectSms = it }
                call.argument<Boolean>("collect_calls")?.let { config.collectCalls = it }
                call.argument<Boolean>("include_body")?.let { config.includeBody = it }
                call.argument<Int>("sub_id")?.let { config.subId = it }
                Keepalive.schedule(this)
                result.success(HostFiles.writeStatus(this).toString())
            }

            "send" -> {
                val request = SendRequest(
                    to = call.argument<String>("to").orEmpty(),
                    body = call.argument<String>("body").orEmpty(),
                    subId = call.argument<Int>("sub_id") ?: Config.SUB_DEFAULT,
                    source = Commands.SOURCE_UI,
                    id = UUID.randomUUID().toString().take(12),
                )
                result.success(Sender.send(this, request).toString())
            }

            "keepaliveNow" -> result.success(Keepalive.fire(this, force = true).toString())

            "checkBalance" -> Balance.refresh(this, force = true) { results ->
                result.success(results.toString())
            }

            "rotateToken" -> {
                val token = config.rotateToken()
                HostFiles.publishToken(this, token)
                result.success(token)
            }

            "requestPermissions" -> {
                requestPermissions(
                    arrayOf(
                        Manifest.permission.SEND_SMS,
                        Manifest.permission.RECEIVE_SMS,
                        Manifest.permission.READ_CALL_LOG,
                        Manifest.permission.READ_PHONE_STATE,
                        Manifest.permission.READ_PHONE_NUMBERS,
                        Manifest.permission.CALL_PHONE,
                    ),
                    PERMISSION_REQUEST,
                )
                result.success(null)
            }

            else -> result.notImplemented()
        }
    }

    private companion object {
        const val CHANNEL = "com.nktkln.rackphone.companion/control"
        const val PERMISSION_REQUEST = 1
    }
}
