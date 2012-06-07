from sockjs.cyclone.conn import ConnectionInfo


class BaseTransportMixin(object):
    """Base transport.

    Implements few methods that session expects to see in each transport.
    """

    name = 'override_me_please'

    def get_conn_info(self):
        """ Return C{ConnectionInfo} object from current transport """
        return ConnectionInfo(self.request.remote_ip,
                              self.request.cookies,
                              self.request.arguments)

    def session_closed(self):
        """ Called by the session, when it gets closed """
        pass

