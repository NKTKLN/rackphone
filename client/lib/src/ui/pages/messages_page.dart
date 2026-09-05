import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/feed_controller.dart';
import '../event_tile.dart';

/// Shows SMS without suggesting that the not-yet-implemented send route works.
class MessagesPage extends StatefulWidget {
  const MessagesPage({required this.controller, super.key});

  final FeedController controller;

  @override
  State<MessagesPage> createState() => _MessagesPageState();
}

class _MessagesPageState extends State<MessagesPage> {
  @override
  void initState() {
    super.initState();
    unawaited(widget.controller.load(kind: 'sms'));
    widget.controller.listen();
  }

  @override
  void dispose() {
    unawaited(widget.controller.stop());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.controller,
    builder: (context, _) {
      final controller = widget.controller;
      final messages = controller.events
          .where((event) => event.kind == 'sms')
          .toList(growable: false);
      return Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(
                'Messages',
                style: Theme.of(context).textTheme.titleLarge,
              ),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: controller.refresh,
              child: messages.isEmpty
                  ? ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: <Widget>[
                        if (controller.loading)
                          const Padding(
                            padding: EdgeInsets.all(32),
                            child: Center(child: CircularProgressIndicator()),
                          )
                        else
                          Padding(
                            padding: const EdgeInsets.all(24),
                            child: Text(
                              controller.failure == null
                                  ? 'Text messages from ${controller.unit} will appear here as they arrive.'
                                  : 'Messages for ${controller.unit} could not be loaded. Pull to refresh and try again.',
                              textAlign: TextAlign.center,
                            ),
                          ),
                      ],
                    )
                  : ListView.builder(
                      physics: const AlwaysScrollableScrollPhysics(),
                      itemCount: messages.length,
                      itemBuilder: (context, index) =>
                          EventTile(event: messages[index]),
                    ),
            ),
          ),
          const SafeArea(
            top: false,
            minimum: EdgeInsets.all(12),
            child: Text('Sending will arrive with the send route.'),
          ),
        ],
      );
    },
  );
}
