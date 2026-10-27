"""Fan-out of Postgres NOTIFY messages to server-sent-event subscribers.

One background thread holds a LISTEN connection. Each notification names a
job id; subscribers waiting on that job are woken through an asyncio queue and
re-read the job snapshot from the database. Notifications are only a wake-up
signal, so a missed one is repaired by the periodic re-read in the SSE loop.
"""

import asyncio
import logging
import threading
from collections import defaultdict

import psycopg

from app.config import get_settings
from app.pipeline.notify import JOB_EVENTS

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._run, name="event-bus", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        with self._lock:
            self._subs[job_id].add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[job_id].discard(q)
            if not self._subs[job_id]:
                del self._subs[job_id]

    def _wake(self, job_id: str) -> None:
        with self._lock:
            queues = list(self._subs.get(job_id, ()))
        for q in queues:
            try:
                q.put_nowait(True)
            except asyncio.QueueFull:
                pass  # a wake-up is already pending

    def _run(self) -> None:
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
        while not self._stop.is_set():
            try:
                with psycopg.connect(dsn, autocommit=True) as conn:
                    conn.execute(f"LISTEN {JOB_EVENTS}")
                    logger.info("event bus listening")
                    while not self._stop.is_set():
                        for note in conn.notifies(timeout=1.0, stop_after=50):
                            if self._loop:
                                self._loop.call_soon_threadsafe(self._wake, note.payload)
            except Exception:  # noqa: BLE001
                logger.exception("event bus connection lost, retrying")
                self._stop.wait(2)


bus = EventBus()
