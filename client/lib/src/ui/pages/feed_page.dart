import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/feed_controller.dart';
import '../event_tile.dart';

/// Shows the selected unit's stored history and live event tail.
class FeedPage extends StatefulWidget {
  const FeedPage({required this.controller, super.key});

  final FeedController controller;

  @override
  State<FeedPage> createState() => _FeedPageState();
}

class _FeedPageState extends State<FeedPage> {
  @override
  void initState() {
    super.initState();
    unawaited(widget.controller.load());
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
      if (controller.loading && controller.events.isEmpty) {
        return const Center(child: CircularProgressIndicator());
      }
      return RefreshIndicator(
        onRefresh: controller.refresh,
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: <Widget>[
            if (controller.failure != null)
              SliverToBoxAdapter(
                child: _Notice(
                  text:
                      'Events for ${controller.unit} could not be loaded. Pull to refresh and try again.',
                ),
              ),
            if (controller.events.isEmpty)
              SliverFillRemaining(
                hasScrollBody: false,
                child: Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      'Messages, calls, and notifications from ${controller.unit} will appear here as they arrive.',
                      textAlign: TextAlign.center,
                    ),
                  ),
                ),
              )
            else
              SliverList.builder(
                itemCount: controller.events.length,
                itemBuilder: (context, index) =>
                    EventTile(event: controller.events[index]),
              ),
          ],
        ),
      );
    },
  );
}

class _Notice extends StatelessWidget {
  const _Notice({required this.text});

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
