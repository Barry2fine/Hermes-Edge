"""Offline committee suite: deny network access, including during collection."""
import socket
from unittest.mock import patch

_patches = []


def _blocked(*args, **kwargs):
    raise AssertionError("network access forbidden in offline suite")


def pytest_sessionstart(session):
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                        (socket, "create_connection"), (socket, "getaddrinfo")):
        replacement = patch.object(owner, name, _blocked)
        replacement.start()
        _patches.append(replacement)


def pytest_sessionfinish(session, exitstatus):
    for replacement in reversed(_patches):
        replacement.stop()
    _patches.clear()
