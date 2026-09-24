import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/files_controller.dart';

typedef ChooseFile = Future<({String name, Uint8List bytes})?> Function();
typedef SaveFile = Future<void> Function(String name, Uint8List bytes);

/// Lists and transfers the occasional operator file for one unit.
class FilesPage extends StatefulWidget {
  const FilesPage({
    required this.controller,
    this.chooseFile,
    this.saveFile,
    super.key,
  });

  final FilesController controller;

  /// Injectable boundaries keep widget tests away from platform dialogs.
  final ChooseFile? chooseFile;
  final SaveFile? saveFile;

  @override
  State<FilesPage> createState() => _FilesPageState();
}

class _FilesPageState extends State<FilesPage> {
  @override
  void initState() {
    super.initState();
    unawaited(widget.controller.refresh());
  }

  Future<void> _upload() async {
    final selected = await (widget.chooseFile ?? _chooseFile)();
    if (selected == null) return;
    await widget.controller.upload(selected.name, selected.bytes);
  }

  Future<void> _download(String name) async {
    final bytes = await widget.controller.download(name);
    if (bytes == null) return;
    await (widget.saveFile ?? _saveFile)(name, bytes);
  }

  Future<void> _confirmRemove(String name) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete $name?'),
        content: const Text(
          'This file may have no second copy. Deleting it cannot be undone.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep file'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed == true) await widget.controller.remove(name);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text('Files · ${widget.controller.unit}'),
      actions: [
        IconButton(
          tooltip: 'Upload file',
          onPressed: _upload,
          icon: const Icon(Icons.upload_file_outlined),
        ),
      ],
    ),
    body: ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final controller = widget.controller;
        if (controller.loading && controller.files.isEmpty) {
          return const Center(child: CircularProgressIndicator());
        }
        return RefreshIndicator(
          onRefresh: controller.refresh,
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              if (controller.failure != null)
                SliverToBoxAdapter(
                  child: _FileNotice(
                    text:
                        'The file operation did not finish. Check the unit connection and try again. ${controller.failure}',
                  ),
                ),
              if (controller.files.isEmpty)
                SliverFillRemaining(
                  hasScrollBody: false,
                  child: Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(
                        '${controller.unit} has no transfer files. This directory is for moving files to and from the phone.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ),
                )
              else
                SliverList.builder(
                  itemCount: controller.files.length,
                  itemBuilder: (context, index) {
                    final file = controller.files[index];
                    return ListTile(
                      title: Text(file.name),
                      subtitle: Text(
                        '${_size(file.size)} · ${_modified(file.modified)}',
                      ),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          IconButton(
                            tooltip: 'Download ${file.name}',
                            onPressed: () => _download(file.name),
                            icon: const Icon(Icons.download_outlined),
                          ),
                          IconButton(
                            tooltip: 'Delete ${file.name}',
                            onPressed: () => _confirmRemove(file.name),
                            icon: const Icon(Icons.delete_outline),
                          ),
                        ],
                      ),
                    );
                  },
                ),
            ],
          ),
        );
      },
    ),
  );
}

/// The system document picker, over the activity's own channel.
///
/// Not a package: the obvious one does not build against the current Android
/// Gradle plugin, and this is two intents on the single platform this app
/// targets - the trade the companion app's pubspec already describes.
const _filesChannel = MethodChannel('com.nktkln.rackphone.client/files');

Future<({String name, Uint8List bytes})?> _chooseFile() async {
  final chosen = await _filesChannel.invokeMapMethod<String, Object?>('pick');
  final name = chosen?['name'];
  final bytes = chosen?['bytes'];
  // A cancelled dialog answers null, which is not a failure.
  if (name is! String || bytes is! Uint8List) return null;
  return (name: name, bytes: bytes);
}

Future<void> _saveFile(String name, Uint8List bytes) async {
  await _filesChannel.invokeMethod<String>('save', {
    'name': name,
    'bytes': bytes,
  });
}

String _size(int bytes) {
  if (bytes < 1024) return '$bytes bytes';
  final kilobytes = bytes / 1024;
  if (kilobytes < 1024) return '${_short(kilobytes)} KB';
  return '${_short(kilobytes / 1024)} MB';
}

String _short(double value) => value >= 10 || value == value.roundToDouble()
    ? value.toStringAsFixed(0)
    : value.toStringAsFixed(1);

String _modified(DateTime value) {
  final local = value.toLocal();
  String two(int number) => number.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)} '
      '${two(local.hour)}:${two(local.minute)}';
}

class _FileNotice extends StatelessWidget {
  const _FileNotice({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(16),
    child: Text(
      text,
      style: TextStyle(color: Theme.of(context).colorScheme.error),
    ),
  );
}
