import heapq
import time


class SessionContainer(object):
    """Session container object.

    If we will implement sessions with Tornado timeouts, for polling transports
    it will be nightmare - if load will be high, number of discarded timeouts
    will be huge and will be huge performance hit, as Tornado will have to
    clean them up all the time.
    """
    def __init__(self):
        self._items = dict()
        self._queue = []

    def add(self, session):
        """Add session to the container.

        `session`
            Session object
        """
        self._items[session.session_id] = session

        if session.expiry is not None:
            heapq.heappush(self._queue, session)

    def get(self, session_id):
        """Return session object or None if it is not available

        `session_id`
            Session identifier
        """
        return self._items.get(session_id, None)

    def remove(self, session_id):
        """Remove session object from the container

        `session_id`
            Session identifier
        """
        session = self._items.get(session_id, None)

        if session is not None:
            session.promoted = -1
            session.on_delete(True)
            del self._items[session_id]
            return True

        return False

    def expire(self, current_time=None):
        """Expire any old entries

        `current_time`
            Optional time to be used to clean up queue (can be used in unit tests)
        """
        if not self._queue:
            return

        if current_time is None:
            current_time = time.time()

        while self._queue:
            # Get top most item
            top = self._queue[0]

            # Early exit if item was not promoted and its expiration time
            # is greater than now.
            if top.promoted is None and top.expiry_date > current_time:
                break

            # Pop item from the stack
            top = heapq.heappop(self._queue)

            need_reschedule = (top.promoted is not None
                               and top.promoted > current_time)

            # Give chance to reschedule
            if not need_reschedule:
                top.promoted = None
                top.on_delete(False)

                need_reschedule = (top.promoted is not None
                                   and top.promoted > current_time)

            # If item is promoted and expiration time somewhere in future
            # just reschedule it
            if need_reschedule:
                top.expiry_date = top.promoted
                top.promoted = None
                heapq.heappush(self._queue, top)
            else:
                del self._items[top.session_id]

