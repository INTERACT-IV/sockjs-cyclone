import random
import hashlib
import time

from twisted.python import log
from twisted.internet import reactor
from twisted.internet import task
from twisted.python.constants import NamedConstant, Names

from sockjs.cyclone import proto


class ConnectionInfo(object):
    """ Connection information object.

    Will be passed to the C{connectionMade} handler of your connection class.

    Has few properties:

    @cvar ip: Caller IP address

    @cvar cookies: Collection of cookies

    @cvar arguments: Collection of the query string arguments
    """
    def __init__(self, ip, cookies, arguments):
        self.ip = ip
        self.cookies = cookies
        self.arguments = arguments

    def get_argument(self, name):
        """ Return single argument by name """
        val = self.arguments.get(name)
        if val:
            return val[0]
        return None

    def get_cookie(self, name):
        """ Return single cookie by its name """
        return self.cookies.get(name)


class SESSION_STATE(Names):
    """
    Constants representing Session States
    """
    CONNECTING = NamedConstant()
    OPEN = NamedConstant()
    CLOSING = NamedConstant()
    CLOSED = NamedConstant()


class BaseSession(object):
    """ Base session implementation class """

    def __init__(self, conn, server):
        """ Base constructor.

        @param conn: Connection class
        @param server: SockJSRouter instance

        """
        self.server = server
        self.stats = server.stats

        self.send_expects_json = False

        self.handler = None
        self.state = SESSION_STATE.CONNECTING

        self.conn_info = None

        self.conn = conn(self)

        self.close_reason = None

    def set_handler(self, handler):
        """Set transport handler

        @param handler: Handler, should derive from the
                        C{sockjs.cyclone.transports.base.BaseTransportMixin}
        """
        if self.handler is not None:
            raise Exception('Attempted to overwrite BaseSession handler')

        self.handler = handler
        self.transport_name = self.handler.name

        if self.conn_info is None:
            self.conn_info = handler.get_conn_info()
            self.stats.on_sess_opened(self.transport_name)

        return True

    def verify_state(self):
        """ Verify if session was not yet opened. If it is, open it and call
        connections C{connectionMade} """
        if self.state == SESSION_STATE.CONNECTING:
            self.state = SESSION_STATE.OPEN

            self.conn.connectionMade(self.conn_info)

    def remove_handler(self, handler):
        """ Remove active handler from the session

        @param handler: Handler to remove
        """
        # Attempt to remove another handler
        if self.handler != handler:
            raise Exception('Attempted to remove invalid handler')

        self.handler = None

    def close(self, code=3000, message='Go away!'):
        """ Close session or endpoint connection.

        @param code: Closing code

        @param message: Close message
        """
        if self.state != SESSION_STATE.CLOSED:
            try:
                self.conn.on_close()
            except:
                log.msg("Failed to call on_close().", exc_info=True)
            finally:
                self.state = SESSION_STATE.CLOSED
                self.close_reason = (code, message)

            # Bump stats
            self.stats.on_sess_closed(self.transport_name)

            # If we have active handler, notify that session was closed
            if self.handler is not None:
                self.handler.session_closed()

    def delayed_close(self):
        """ Delayed close - won't close immediately, but on the next reactor
        loop. """
        self.state = SESSION_STATE.CLOSING
        reactor.callLater(0, self.close)

    def get_close_reason(self):
        """ Return last close reason tuple.

        For example:

            if self.session.is_closed:
                code, reason = self.session.get_close_reason()

        """
        if self.close_reason:
            return self.close_reason

        return (3000, 'Go away!')

    @property
    def is_closed(self):
        """ Check if session was closed. """
        return (self.state == SESSION_STATE.CLOSED 
                or self.state == SESSION_STATE.CLOSING)

    def send_message(self, msg, stats=True):
        """ Send or queue outgoing message

        @param msg: Message to send

        @param stats: If set to True, will update statistics after operation
                      completes
        """
        raise NotImplemented()

    def send_jsonified(self, msg, stats=True):
        """ Send or queue outgoing message which was json-encoded before. Used
        by the C{broadcast} method.

        @param msg: JSON-encoded message to send
        @param stats: If set to True, will update statistics after operation
                      completes
        """
        raise NotImplemented()

    def broadcast(self, clients, msg):
        """ Optimized broadcast implementation. Depending on type of the
        session, will json-encode message once and will call either
        C{send_message} or C{send_jsonifed}.

        @param clients: Clients iterable
        @param msg: Message to send

        """
        self.server.broadcast(clients, msg)


class SessionMixin(object):
    """ Represents one session object stored in the session container.
        Derive from this object to store additional data.
    """

    def __init__(self, session_id=None, expiry=None, time_module=time):
        """ Constructor.

        @param session_id: Optional session id. If not provided, will generate
                           new session id.

        @param expiry: Expiration time in seconds. If not provided, will never
                       expire.

        @param time_module: only used for unit testing. A provider for a time()
                            function
        """
        self.time_module = time_module

        self.session_id = session_id or self._random_key()
        self.promoted = None
        self.expiry = expiry

        if self.expiry is not None:
            self.expiry_date = self.time_module.time() + self.expiry

    def _random_key(self):
        """ Return random session key """
        hashstr = '%s%s' % (random.random(), self.time_module.time())
        return hashlib.md5(hashstr).hexdigest()

    def is_alive(self):
        """ Check if session is still alive """
        return self.expiry_date > self.time_module.time()

    def promote(self):
        """ Mark object as alive, so it won't be collected during next
        run of the garbage collector.
        """
        if self.expiry is not None:
            self.promoted = self.time_module.time() + self.expiry

    def on_delete(self, forced):
        """ Triggered when object was expired or deleted. """
        pass

    def __cmp__(self, other):
        return cmp(self.expiry_date, other.expiry_date)

    def __repr__(self):
        return '%f %s %d' % (getattr(self, 'expiry_date', -1),
                             self.session_id,
                             self.promoted or 0)


class Session(BaseSession, SessionMixin):
    """ SockJS session implementation. """

    def __init__(self, conn, server, session_id, expiry=None):
        """ Session constructor.

        @param conn: Default connection class

        @param server: `SockJSRouter` instance

        @param session_id: Session id

        @param expiry: Session expiry time

        """
        # Initialize session
        self.send_queue = ''  # TODO: use a list of strings or a StringIO
        self.send_expects_json = True

        # Heartbeat related stuff
        self._heartbeat_timer = None
        self._heartbeat_interval = server.settings['heartbeat_delay']*1000

        self._immediate_flush = server.settings['immediate_flush']
        self._pending_flush = False

        self._verify_ip = server.settings['verify_ip']

        SessionMixin.__init__(self, session_id, expiry)
        BaseSession.__init__(self, conn, server)


    # Session callbacks
    def on_delete(self, forced):
        """ Session expiration callback

        @param forced: If session item explicitly deleted, forced will be set to
                       C{True}. If item expired, will be set to C{False}.
        """
        # Do not remove connection if it was not forced and there's a running
        # connection
        if not forced and self.handler is not None and not self.is_closed:
            self.promote()
        else:
            self.close()

    # Add session
    def set_handler(self, handler, start_heartbeat=True):
        """ Set active handler for the session

        @param handler: Associate active cyclone handler with the session

        @param start_heartbeat: Should session start heartbeat immediately
        """
        # Check if session already has associated handler
        if self.handler is not None:
            handler.send_pack(proto.disconnect(2010,
                                               "Another connection still open"))
            return False

        if self._verify_ip and self.conn_info is not None:
            # If IP address doesn't match - refuse connection
            if handler.request.remote_ip != self.conn_info.ip:
                log.msg('Attempted to attach to session %s (%s) from '
                        'different IP (%s)' % ( self.session_id,
                                                self.conn_info.ip,
                                                handler.request.remote_ip
                                              )
                       )

                handler.send_pack(proto.disconnect(2010, 'Attempted to connect '
                                         'to session from different IP'))
                return False

        if ( self.state == SESSION_STATE.CLOSING
             or self.state == SESSION_STATE.CLOSED):
            handler.send_pack(proto.disconnect(*self.get_close_reason()))
            return False

        # Associate handler and promote session
        super(Session, self).set_handler(handler)

        self.promote()

        if start_heartbeat:
            self.start_heartbeat()

        return True

    def verify_state(self):
        """ Verify if session was not yet opened. If it is, open it and call
        connections C{connectionMade} """
        # If we're in CONNECTING state - send 'o' message to the client
        if self.state == SESSION_STATE.CONNECTING:
            self.handler.send_pack(proto.CONNECT)

        # Call parent implementation
        super(Session, self).verify_state()

    def remove_handler(self, handler):
        """ Detach active handler from the session

        @param handler: Handler to remove
        """
        super(Session, self).remove_handler(handler)

        self.promote()
        self.stop_heartbeat()

    def send_message(self, msg, stats=True):
        """ Send or queue outgoing message

        @param msg: Message to send

        @param stats: If set to True, will update statistics after operation
                      completes
        """
        self.send_jsonified(proto.json_encode(msg), stats)

    def send_jsonified(self, msg, stats=True):
        """ Send JSON-encoded message

        @param msg: JSON encoded string to send
        @param stats: If set to True, will update statistics after operation
                      completes
        """
        assert isinstance(msg, basestring), 'Can only send strings'

        if isinstance(msg, unicode):
            msg = msg.encode('utf-8')

        if self._immediate_flush:
            if self.handler and not self.send_queue:
                # Send message right away
                self.handler.send_pack('a[%s]' % msg)
            else:
                if self.send_queue:
                    self.send_queue += ','
                self.send_queue += msg

                self.flush()
        else:
            if self.send_queue:
                self.send_queue += ','
            self.send_queue += msg

            if not self._pending_flush:
                reactor.callLater(0, self.flush)
                self._pending_flush = True

        if stats:
            self.stats.on_pack_sent(1)

    def flush(self):
        """ Flush message queue if there's an active connection running """
        self._pending_flush = False

        if self.handler is None:
            return

        if not self.send_queue:
            return

        self.handler.send_pack('a[%s]' % self.send_queue)
        self.send_queue = ''

    def close(self, code=3000, message='Go away!'):
        """ Close session.

        @param code: Closing code

        @param message: Closing message
        """
        if self.state != SESSION_STATE.CLOSED:
            # Notify handler
            if self.handler is not None:
                self.handler.send_pack(proto.disconnect(code, message))

        super(Session, self).close(code, message)

    # Heartbeats
    def start_heartbeat(self):
        """ Reset hearbeat timer """
        self.stop_heartbeat()

        self._heartbeat_timer = task.LoopingCall(self._heartbeat)
        self._heartbeat_timer.start(self._heartbeat_interval, False)

    def stop_heartbeat(self):
        """ Stop active heartbeat """
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.stop()
            self._heartbeat_timer = None

    def delay_heartbeat(self):
        """ Delay active heartbeat """
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.reset()

    def _heartbeat(self):
        """ Heartbeat callback """
        if self.handler is not None:
            self.handler.send_pack(proto.HEARTBEAT)
        else:
            self.stop_heartbeat()

    def messagesReceived(self, msg_list):
        """ Handle incoming messages

        @param msg_list: Message list to process
        """
        self.stats.on_pack_recv(len(msg_list))

        for msg in msg_list:
            self.conn.messageReceived(msg)

