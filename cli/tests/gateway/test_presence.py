from rackphone.gateway.presence import ClientPresence


def test_presence_tracks_open_streams_and_the_grace_window() -> None:
    presence = ClientPresence()

    assert presence.is_watched(100) is False
    presence.opened()
    presence.opened()
    assert presence.is_watched(100) is True

    presence.closed(110)
    assert presence.is_watched(1_000) is True
    presence.closed(120)
    assert presence.is_watched(180) is True
    assert presence.is_watched(181) is False


def test_an_unbalanced_close_does_not_raise() -> None:
    # Raising here would run inside the cleanup of a dropped connection, hide
    # why the stream ended, and leave the gateway believing it is being watched.
    presence = ClientPresence()
    presence.closed(1_000)
    # And it invents no watcher: nothing was open, so nothing is in grace.
    assert presence.is_watched(1_000) is False
