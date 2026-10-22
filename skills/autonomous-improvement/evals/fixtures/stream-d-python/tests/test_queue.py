import pytest

from src.queue import Queue


def test_fifo_order():
    queue = Queue()
    queue.push(1)
    queue.push(2)
    assert queue.pop() == 1
    assert queue.pop() == 2


def test_empty_pop_raises():
    with pytest.raises(IndexError):
        Queue().pop()
