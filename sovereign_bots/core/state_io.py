"""Local process/thread serialization and crash-safe JSON replacement.

Locks are advisory: every writer must use this protocol. Network filesystems
and distributed hosts require a transactional shared store instead.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time

_guard = threading.Lock()
_locks = {}
_local = threading.local()


def _reset_after_fork():
    # A child owns neither its parent's RLocks nor its reentrant lock depths.
    # Reset even the registry guard, which another thread may have held at fork.
    global _guard, _locks, _local
    _guard = threading.Lock()
    _locks = {}
    _local = threading.local()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


@contextmanager
def state_lock(path, timeout=5.0):
    owner_pid = os.getpid()
    key = os.path.normcase(str(Path(path).resolve()))
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError("knowledge state lock timeout")
    depths = getattr(_local, "depths", {})
    _local.depths = depths
    try:
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        Path(key).parent.mkdir(parents=True, exist_ok=True)
        with open(key + ".lock", "a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("knowledge state lock timeout") from None
                    time.sleep(0.02)
            depths[key] = 1
            try:
                yield
            finally:
                del depths[key]
                # A forked child may unwind an inherited context. Its copy of
                # the descriptor must not explicitly unlock the parent's lock.
                if os.getpid() == owner_pid:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if os.getpid() == owner_pid:
            lock.release()


def atomic_json(path, data):
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
