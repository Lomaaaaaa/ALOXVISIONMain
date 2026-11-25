"""alox.events: Event, EventBus and Subscription (lossless per-subscriber queues, bounded queues, prefix filters,
callbacks)."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Any

import pytest

from alox.events import Event, EventBus, Publish, Subscription


def ev(topic: str, **data: Any) -> Event:
    return Event(topic, "test", data)


def topics(events: list[Event]) -> list[str]:
    return [e.topic for e in events]


def run_with_deadline(fn: Any, seconds: float = 1.0) -> None:
    """Run ``fn`` in a thread and fail (instead of hanging) when it does not return in time (a deadlock)."""
    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn()
        except BaseException as exc:                                # noqa: BLE001 - re-raised below
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "deadlock: the call did not return"
    if errors:
        raise errors[0]


# ================================================================================================== Event
class TestEvent:
    def test_fields_and_defaults(self) -> None:
        before = time.time()
        event = Event("device.z.state", "z")
        assert event.topic == "device.z.state"
        assert event.source == "z"
        assert dict(event.data) == {}
        assert before <= event.time <= time.time()

    def test_default_data_is_not_shared(self) -> None:
        assert Event("a", "s").data is not Event("b", "s").data

    def test_is_frozen(self) -> None:
        event = ev("a")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.topic = "b"                                       # type: ignore[misc]

    @pytest.mark.parametrize(("topic", "prefixes", "expected"), [
        ("device.z.state", (), True),
        ("device.z.state", ("device",), True),
        ("device.z.state", ("device.z",), True),
        ("device.z.state", ("device.z.state",), True),
        ("device.z.state", ("app", "device.z"), True),
        ("device.z.state", ("device.zz",), False),
        ("device.zz.state", ("device.z",), False),                 # a prefix ends at a dot, not mid-word
        ("devices.z", ("device",), False),
        ("device.z.state", ("device.z.state.x",), False),
        ("device.z.state", ("app", "job"), False),
        ("device", ("device",), True),
    ])
    def test_matches(self, topic: str, prefixes: tuple[str, ...], expected: bool) -> None:
        assert ev(topic).matches(prefixes) is expected


# ================================================================================================== subscriptions
class TestSubscription:
    def test_receives_every_event_in_order(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        for i in range(200):
            bus.publish(ev("t", i=i))
        assert [e.data["i"] for e in sub.drain()] == list(range(200))
        assert sub.dropped == 0

    def test_unbounded_queue_is_lossless(self) -> None:
        bus = EventBus()
        sub = bus.subscribe(["x"])
        for i in range(10_000):
            bus.publish(ev("x.y", i=i))
        received = sub.drain()
        assert len(received) == 10_000
        assert received[-1].data["i"] == 9_999
        assert sub.dropped == 0

    def test_prefix_filter(self) -> None:
        bus = EventBus()
        z = bus.subscribe(["device.z"])
        devices = bus.subscribe(["device"])
        several = bus.subscribe(["device.xy", "app"])
        everything = bus.subscribe()
        published = ["device.z.state", "device.xy.state", "device.zz.state", "app.started", "application.x", "device"]
        for topic in published:
            bus.publish(ev(topic))
        assert topics(z.drain()) == ["device.z.state"]
        assert topics(devices.drain()) == ["device.z.state", "device.xy.state", "device.zz.state", "device"]
        assert topics(several.drain()) == ["device.xy.state", "app.started"]
        assert topics(everything.drain()) == published

    def test_subscribe_accepts_any_iterable_of_prefixes(self) -> None:
        bus = EventBus()
        sub = bus.subscribe(p for p in ["a", "b"])
        assert sub.prefixes == ("a", "b")
        bus.publish(ev("b.1"))
        bus.publish(ev("c.1"))
        assert topics(sub.drain()) == ["b.1"]

    def test_each_subscriber_has_its_own_queue(self) -> None:
        bus = EventBus()
        a = bus.subscribe()
        b = bus.subscribe()
        bus.publish(ev("one"))
        bus.publish(ev("two"))
        assert topics(a.drain()) == ["one", "two"]
        assert topics(b.drain()) == ["one", "two"]          # reading ``a`` consumed nothing of ``b``

    def test_subscriber_only_sees_events_published_after_subscribing(self) -> None:
        bus = EventBus()
        bus.publish(ev("early"))
        sub = bus.subscribe()
        bus.publish(ev("late"))
        assert topics(sub.drain()) == ["late"]

    def test_get_returns_queued_event_without_waiting(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        bus.publish(ev("a"))
        bus.publish(ev("b"))
        first = sub.get(timeout=0)
        second = sub.get(timeout=0)
        assert first is not None and first.topic == "a"
        assert second is not None and second.topic == "b"

    def test_get_times_out_with_none(self) -> None:
        sub = EventBus().subscribe()
        assert sub.get(timeout=0) is None
        start = time.monotonic()
        assert sub.get(timeout=0.02) is None
        assert time.monotonic() - start < 0.5

    def test_get_waits_for_an_event_published_by_another_thread(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        publisher = threading.Timer(0.02, bus.publish, args=(ev("late"),))
        publisher.start()
        try:
            event = sub.get(timeout=2)
        finally:
            publisher.join(2)
        assert event is not None and event.topic == "late"

    def test_drain_empties_the_queue_and_iteration_drains(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        assert sub.drain() == []
        bus.publish(ev("a"))
        bus.publish(ev("b"))
        assert [e.topic for e in sub] == ["a", "b"]
        assert sub.drain() == []
        bus.publish(ev("c"))
        assert topics(sub.drain()) == ["c"]

    def test_bounded_queue_drops_the_oldest_and_counts(self) -> None:
        bus = EventBus()
        sub = bus.subscribe(maxsize=3)
        for i in range(5):
            bus.publish(ev("t", i=i))
        assert sub.dropped == 2
        assert [e.data["i"] for e in sub.drain()] == [2, 3, 4]
        bus.publish(ev("t", i=5))                               # room again: nothing more dropped
        assert sub.dropped == 2
        assert [e.data["i"] for e in sub.drain()] == [5]

    def test_bounded_queue_does_not_affect_other_subscribers(self) -> None:
        bus = EventBus()
        small = bus.subscribe(maxsize=1)
        full = bus.subscribe()
        for i in range(10):
            bus.publish(ev("t", i=i))
        assert [e.data["i"] for e in small.drain()] == [9]
        assert small.dropped == 9
        assert len(full.drain()) == 10
        assert full.dropped == 0

    def test_filtered_out_events_are_not_counted_as_dropped(self) -> None:
        bus = EventBus()
        sub = bus.subscribe(["a"], maxsize=1)
        for _ in range(5):
            bus.publish(ev("b.x"))
        assert sub.dropped == 0
        assert sub.drain() == []

    def test_bounded_queue_with_a_concurrent_reader_accounts_for_every_event(self) -> None:
        bus = EventBus()
        sub = bus.subscribe(maxsize=10)
        total = 5_000
        received: list[int] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                event = sub.get(timeout=0.005)
                if event is not None:
                    received.append(event.data["i"])

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        for i in range(total):
            bus.publish(ev("t", i=i))
        stop.set()
        thread.join(2)
        assert not thread.is_alive()
        received.extend(e.data["i"] for e in sub.drain())
        assert len(received) + sub.dropped == total               # every event read or counted as dropped
        assert received == sorted(set(received))                  # in order, none twice
        assert received[-1] == total - 1                          # the newest is never the one dropped

    def test_close_stops_delivery_and_is_idempotent(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        other = bus.subscribe()
        bus.publish(ev("before"))
        sub.close()
        assert sub.closed
        bus.publish(ev("after"))
        assert topics(sub.drain()) == ["before"]                  # what was queued stays readable
        assert topics(other.drain()) == ["before", "after"]
        sub.close()
        bus.publish(ev("again"))
        assert sub.drain() == []
        assert topics(other.drain()) == ["again"]

    def test_subscription_type(self) -> None:
        sub = EventBus().subscribe(["a"], maxsize=4)
        assert isinstance(sub, Subscription)
        assert sub.maxsize == 4
        assert sub.dropped == 0
        assert not sub.closed


# ================================================================================================== callbacks
class TestCallbacks:
    def test_callback_runs_in_the_publisher_thread_for_matching_events(self) -> None:
        bus = EventBus()
        seen: list[tuple[str, int]] = []
        bus.on(["device.z"], lambda e: seen.append((e.topic, threading.get_ident())))
        publisher = threading.Thread(target=lambda: [bus.publish(ev(t)) for t in ("device.z.state", "device.xy.s")])
        publisher.start()
        publisher.join(2)
        assert seen == [("device.z.state", publisher.ident)]

    def test_empty_prefixes_receive_everything(self) -> None:
        bus = EventBus()
        seen: list[Event] = []
        bus.on([], seen.append)
        bus.publish(ev("a"))
        bus.publish(ev("b.c"))
        assert topics(seen) == ["a", "b.c"]

    def test_remove_stops_the_callback_and_is_harmless_twice(self) -> None:
        bus = EventBus()
        seen: list[Event] = []
        remove = bus.on(["a"], seen.append)
        bus.publish(ev("a.1"))
        remove()
        bus.publish(ev("a.2"))
        remove()
        bus.publish(ev("a.3"))
        assert topics(seen) == ["a.1"]

    def test_failing_callback_is_logged_and_does_not_stop_delivery(self, caplog: pytest.LogCaptureFixture) -> None:
        bus = EventBus()
        before: list[Event] = []
        after: list[Event] = []
        sub = bus.subscribe()

        def broken(_event: Event) -> None:
            raise RuntimeError("listener bug")

        bus.on([], before.append)
        bus.on([], broken)
        bus.on([], after.append)
        with caplog.at_level(logging.ERROR, logger="alox.events"):
            bus.publish(ev("x.y"))                              # does not raise
        assert topics(before) == ["x.y"]
        assert topics(after) == ["x.y"]
        assert topics(sub.drain()) == ["x.y"]
        assert "event callback failed for x.y" in caplog.text
        assert "listener bug" in caplog.text

    def test_callback_may_publish_subscribe_and_remove_itself(self) -> None:
        bus = EventBus()
        seen: list[str] = []
        late: list[Subscription] = []

        def callback(event: Event) -> None:
            seen.append(event.topic)
            if event.topic == "first":
                late.append(bus.subscribe())
                remove()
                bus.publish(ev("second"))                       # re-entrant publish from inside a callback

        remove = bus.on([], callback)
        run_with_deadline(lambda: bus.publish(ev("first")))
        bus.publish(ev("third"))
        assert seen == ["first"]                                # removed before "second" was published
        assert topics(late[0].drain()) == ["second", "third"]

    def test_subscriber_may_close_from_a_callback(self) -> None:
        bus = EventBus()
        sub = bus.subscribe()
        bus.on([], lambda e: sub.close() if e.topic == "stop" else None)
        run_with_deadline(lambda: bus.publish(ev("stop")))
        bus.publish(ev("after"))
        assert topics(sub.drain()) == ["stop"]


# ================================================================================================== threads
def test_concurrent_publishers_lose_nothing_and_keep_each_publishers_order() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    callback_count = 0
    count_lock = threading.Lock()

    def count(_event: Event) -> None:
        nonlocal callback_count
        with count_lock:
            callback_count += 1

    bus.on([], count)
    publishers, per_publisher = 8, 500
    barrier = threading.Barrier(publishers)

    def publish_all(k: int) -> None:
        barrier.wait(2)
        for i in range(per_publisher):
            bus.publish(Event(f"p.{k}", f"p{k}", {"i": i}))

    threads = [threading.Thread(target=publish_all, args=(k,)) for k in range(publishers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    received = sub.drain()
    assert len(received) == publishers * per_publisher
    assert callback_count == publishers * per_publisher
    for k in range(publishers):
        assert [e.data["i"] for e in received if e.source == f"p{k}"] == list(range(per_publisher))


def test_publish_protocol_accepts_bus_and_list_append() -> None:
    bus = EventBus()
    sink: list[Event] = []
    sub = bus.subscribe()
    publishers: list[Publish] = [bus.publish, sink.append]
    for publish in publishers:
        publish(ev("p"))
    assert topics(sink) == ["p"]
    assert topics(sub.drain()) == ["p"]
