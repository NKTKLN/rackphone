import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:rackphone_client/src/screen/decoder.dart';
import 'package:rackphone_client/src/screen/protocol.dart';

void main() {
  const portrait = DeviceInfo(
    name: 'rack-01',
    codec: 'h264',
    width: 1080,
    height: 2400,
  );
  final packet = VideoPacket(
    pts: 1,
    isConfig: true,
    isKeyFrame: false,
    data: <int>[0, 0, 0, 1, 103],
  );

  test('fake receives configure, feed, and dispose in order', () async {
    final decoder = FakeScreenDecoder();
    await decoder.configure(portrait);
    await decoder.feed(packet);
    await decoder.dispose();

    expect(decoder.calls[0], isA<ConfigureDecoderCall>());
    expect(decoder.calls[1], isA<FeedDecoderCall>());
    expect(decoder.calls[2], isA<DisposeDecoderCall>());
    expect(decoder.textureId, isNull);
  });

  test('packets are dropped before configure', () async {
    final decoder = FakeScreenDecoder();
    await decoder.feed(packet);
    expect(decoder.calls, isEmpty);
  });

  test('changed dimensions mark configuration as a reconfigure', () async {
    final decoder = FakeScreenDecoder();
    await decoder.configure(portrait);
    await decoder.configure(
      const DeviceInfo(
        name: 'rack-01',
        codec: 'h264',
        width: 2400,
        height: 1080,
      ),
    );

    final first = decoder.calls[0] as ConfigureDecoderCall;
    final second = decoder.calls[1] as ConfigureDecoderCall;
    expect(first.reconfigure, isFalse);
    expect(second.reconfigure, isTrue);
  });

  test('a size Android reports after a rotation reaches Dart', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    const name = 'com.nktkln.rackphone.client/screen';
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMessageHandler(
      name,
      (_) async => ByteData(8)..setInt64(0, 7, Endian.big),
    );
    addTearDown(() => messenger.setMockMessageHandler(name, null));
    final decoder = HardwareScreenDecoder();
    final sizes = <({int width, int height})>[];
    decoder.sizes.listen(sizes.add);
    await decoder.configure(portrait);

    await messenger.handlePlatformMessage(
      name,
      ByteData(9)
        ..setUint8(0, 4)
        ..setUint32(1, 2400, Endian.big)
        ..setUint32(5, 1080, Endian.big),
      (_) {},
    );
    await Future<void>.delayed(Duration.zero);

    expect(decoder.textureId, 7);
    expect(sizes, [(width: 2400, height: 1080)]);
  });
}
