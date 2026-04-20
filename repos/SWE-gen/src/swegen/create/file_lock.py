from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def file_lock(lock_path: Path, timeout: float = 600.0, poll_interval: float = 0.1) -> Iterator[None]:
    """Acquire a cross-process exclusive file lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as handle:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if timeout > 0 and (time.monotonic() - start) >= timeout:
                    raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
                time.sleep(poll_interval)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_semaphore(
    lock_dir: Path,
    max_concurrent: int | None = None,
    timeout: float = 1800.0,
    poll_interval: float = 0.5,
) -> Iterator[int]:
    """Acquire one of N file-lock slots (cross-process semaphore).

    Creates ``max_concurrent`` slot files under *lock_dir* and tries to
    exclusively lock one of them.  Multiple processes can hold different
    slots simultaneously, giving controlled concurrency.

    Yields the slot index that was acquired.
    """
    if max_concurrent is None:
        max_concurrent = int(os.environ.get("SWEGEN_DOCKER_CONCURRENCY", "8"))
    max_concurrent = max(max_concurrent, 1)

    lock_dir.mkdir(parents=True, exist_ok=True)
    slot_paths = [lock_dir / f"harbor.slot.{i}" for i in range(max_concurrent)]

    start = time.monotonic()
    handle = None
    slot_idx = -1

    while True:
        for i, slot_path in enumerate(slot_paths):
            fh = open(slot_path, "a+", encoding="utf-8")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                handle = fh
                slot_idx = i
                break
            except BlockingIOError:
                fh.close()

        if handle is not None:
            break

        if timeout > 0 and (time.monotonic() - start) >= timeout:
            raise TimeoutError(
                f"Timed out waiting for semaphore slot in {lock_dir} "
                f"(max_concurrent={max_concurrent})"
            )
        time.sleep(poll_interval)

    try:
        yield slot_idx
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
