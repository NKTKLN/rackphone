"""One vocabulary for failures at the boundary between host and phone.

Sending a message and moving a file fail in the same two ways: the request was
wrong, or the phone was. Two modules had grown their own exception pair and the
API had grown two copies of the rule that turns one into a status code - and a
rule kept in two places is a rule that ends up meaning two things.
"""

from __future__ import annotations


class DeviceBoundaryError(RuntimeError):
    """A refusal an operator can be shown, raised at the device boundary."""

    # False means the caller asked for something impossible; True means the
    # gateway is fine and the phone is not. The distinction is what tells an
    # operator where to look, so it lives on the exception rather than being
    # re-derived from its message.
    device_failure = False
