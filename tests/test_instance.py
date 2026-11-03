"""Only one copy of the bridge at a time."""
from __future__ import annotations

import os

from bridge.instance import InstanceLock


def test_a_second_copy_cannot_take_the_lock(tmp_path):
    first = InstanceLock(tmp_path / "bridge.lock")
    second = InstanceLock(tmp_path / "bridge.lock")

    assert first.acquire() is True
    assert second.acquire() is False
    first.release()


def test_the_lock_is_free_again_once_released(tmp_path):
    first = InstanceLock(tmp_path / "bridge.lock")
    first.acquire()
    first.release()

    second = InstanceLock(tmp_path / "bridge.lock")
    assert second.acquire() is True
    second.release()


def test_the_copy_holding_the_lock_can_be_named(tmp_path):
    holder = InstanceLock(tmp_path / "bridge.lock")
    holder.acquire()

    assert InstanceLock(tmp_path / "bridge.lock").holder() == os.getpid()
    holder.release()


def test_nobody_is_named_once_the_holder_has_gone(tmp_path):
    holder = InstanceLock(tmp_path / "bridge.lock")
    holder.acquire()
    holder.release()

    assert InstanceLock(tmp_path / "bridge.lock").holder() is None
