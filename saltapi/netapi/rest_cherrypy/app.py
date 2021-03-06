'''
A REST API for Salt
===================

.. py:currentmodule:: saltapi.netapi.rest_cherrypy.app

:depends:   - CherryPy Python module
:optdepends:    - ws4py Python module for websockets support.
:configuration: All authentication is done through Salt's :ref:`external auth
    <acl-eauth>` system which requires additional configuration not described
    here.

    Example production-ready configuration; add to the Salt master config file:

    .. code-block:: yaml

        rest_cherrypy:
          port: 8000
          ssl_crt: /etc/pki/tls/certs/localhost.crt
          ssl_key: /etc/pki/tls/certs/localhost.key

    Using only a secure HTTPS connection is strongly recommended since Salt
    authentication credentials will be sent over the wire.

    A self-signed certificate can be generated using the
    :py:func:`~salt.modules.tls.create_self_signed_cert` function in Salt (note
    the dependencies for this module).

    .. code-block:: bash

        % salt-call tls.create_self_signed_cert

    All available configuration options are detailed below. These settings
    configure the CherryPy HTTP server and do not apply when using an external
    server such as Apache or Nginx.

    port
        **Required**

        The port for the webserver to listen on.
    host : ``0.0.0.0``
        The socket interface for the HTTP server to listen on.

        .. versionadded:: 0.8.2
    debug : ``False``
        Starts the web server in development mode. It will reload itself when
        the underlying code is changed and will output more debugging info.
    ssl_crt
        The path to a SSL certificate. (See below)
    ssl_key
        The path to the private key for your SSL certificate. (See below)
    disable_ssl
        A flag to disable SSL. Warning: your Salt authentication credentials
        will be sent in the clear!

        .. versionadded:: 0.8.3
    webhook_disable_auth : False
        The :py:class:`Webhook` URL requires authentication by default but
        external services cannot always be configured to send authentication.
        See the Webhook documentation for suggestions on securing this
        interface.

        .. versionadded:: 0.8.4.1
    webhook_url : /hook
        Configure the URL endpoint for the :py:class:`Webhook` entry point.

        .. versionadded:: 0.8.4.1
    thread_pool : ``100``
        The number of worker threads to start up in the pool.

        .. versionchanged:: 0.8.4
            Previous versions defaulted to a pool of ``10``
    socket_queue_size : ``30``
        Specify the maximum number of HTTP connections to queue.

        .. versionchanged:: 0.8.4
            Previous versions defaulted to ``5`` connections.
    max_request_body_size : ``1048576``
        .. versionchanged:: 0.8.4
            Previous versions defaulted to ``104857600`` for the size of the
            request body
    collect_stats : False
        Collect and report statistics about the CherryPy server

        .. versionadded:: 0.8.4

        Reports are available via the :py:class:`Stats` URL.
    static
        A filesystem path to static HTML/JavaScript/CSS/image assets.
    static_path : ``/static``
        The URL prefix to use when serving static assets out of the directory
        specified in the ``static`` setting.

        .. versionadded:: 0.8.2
    app
        A filesystem path to an HTML file that will be served as a static file.
        This is useful for bootstrapping a single-page JavaScript app.

        .. versionadded:: 0.8.2
    app_path : ``/app``
        The URL prefix to use for serving the HTML file specified in the ``app``
        setting. This should be a simple name containing no slashes.

        Any path information after the specified path is ignored; this is
        useful for apps that utilize the HTML5 history API.

        .. versionadded:: 0.8.2
    root_prefix : ``/``
        A URL path to the main entry point for the application. This is useful
        for serving multiple applications from the same URL.

        .. versionadded:: 0.8.4

.. _rest_cherrypy-auth:

Authentication
--------------

Authentication is performed by passing a session token with each request.
Tokens are generated via the :py:class:`Login` URL.

The token may be sent in one of two ways:

* Include a custom header named :mailheader:`X-Auth-Token`.
* Sent via a cookie. This option is a convenience for HTTP clients that
  automatically handle cookie support (such as browsers).

.. seealso:: You can bypass the session handling via the :py:class:`Run` URL.

Usage
-----

Commands are sent to a running Salt master via this module by sending HTTP
requests to the URLs detailed below.

.. admonition:: Content negotiation

    This REST interface is flexible in what data formats it will accept as well
    as what formats it will return (e.g., JSON, YAML, x-www-form-urlencoded).

    * Specify the format of data in the request body by including the
      :mailheader:`Content-Type` header.
    * Specify the desired data format for the response body with the
      :mailheader:`Accept` header.

Data sent in :http:method:`post` and :http:method:`put` requests  must be in
the format of a list of lowstate dictionaries. This allows multiple commands to
be executed in a single HTTP request.

.. glossary::

    lowstate
        A dictionary containing various keys that instruct Salt which command
        to run, where that command lives, any parameters for that command, any
        authentication credentials, what returner to use, etc.

        Salt uses the lowstate data format internally in many places to pass
        command data between functions. Salt also uses lowstate for the
        :ref:`LocalClient() <python-api>` Python API interface.

The following example (in JSON format) causes Salt to execute two commands::

    [{
        "client": "local",
        "tgt": "*",
        "fun": "test.fib",
        "arg": ["10"]
    },
    {
        "client": "runner",
        "fun": "jobs.lookup_jid",
        "jid": "20130603122505459265"
    }]

.. admonition:: x-www-form-urlencoded

    Sending JSON or YAML in the request body is simple and most flexible,
    however sending data in urlencoded format is also supported with the
    caveats below. It is the default format for HTML forms, many JavaScript
    libraries, and the :command:`curl` command.

    For example, the equivalent to running ``salt '*' test.ping`` is sending
    ``fun=test.ping&arg&client=local&tgt=*`` in the HTTP request body.

    Caveats:

    * Only a single command may be sent per HTTP request.
    * Repeating the ``arg`` parameter multiple times will cause those
      parameters to be combined into a single list.

      Note, some popular frameworks and languages (notably jQuery, PHP, and
      Ruby on Rails) will automatically append empty brackets onto repeated
      parameters. E.g., ``arg=one``, ``arg=two`` will be sent as ``arg[]=one``,
      ``arg[]=two``. This is not supported; send JSON or YAML instead.


.. |req_token| replace:: a session token from :py:class:`~Login`.
.. |req_accept| replace:: the desired response format.
.. |req_ct| replace:: the format of the request body.

.. |res_ct| replace:: the format of the response body; depends on the
    :mailheader:`Accept` request header.

.. |200| replace:: success
.. |401| replace:: authentication required
.. |406| replace:: requested Content-Type not available
'''
# We need a custom pylintrc here...
# pylint: disable=W0212,E1101,C0103,R0201,W0221,W0613

# Import Python libs
import collections
import itertools
import functools
import logging
import json
import time
from multiprocessing import Process, Pipe

# Import third-party libs
import cherrypy
from cherrypy.lib import cpstats
import yaml

# Import Salt libs
import salt
import salt.auth
import salt.utils.event

# Import salt-api libs
import saltapi

logger = logging.getLogger(__name__)

# Imports related to websocket
try:
    from .tools import websockets
    import event_processor
    from ws4py.server.cherrypyserver import WebSocketPlugin, WebSocketTool

    HAS_WEBSOCKETS = True
except ImportError:
    logger.debug('Error setting up websockets', exc_info=True)

    websockets = type('websockets', (object,), {
        'SynchronizingWebsocket': None,
    })

    HAS_WEBSOCKETS = False



def salt_token_tool():
    '''
    If the custom authentication header is supplied, put it in the cookie dict
    so the rest of the session-based auth works as intended
    '''
    x_auth = cherrypy.request.headers.get('X-Auth-Token', None)

    # X-Auth-Token header trumps session cookie
    if x_auth:
        cherrypy.request.cookie['session_id'] = x_auth

def salt_ip_verify_tool():
    '''
    If there is a list of restricted IPs, verify current
    client is coming from one of those IPs.
    '''
    # This is overly cumbersome and crude,
    # But, it's also safe... ish...
    salt_config = cherrypy.config.get('saltopts', None)
    if salt_config:
        cherrypy_conf = salt_config.get('rest_cherrypy', None)
        if cherrypy_conf:
            auth_ip_list = cherrypy_conf.get('authorized_ips', None)
            if auth_ip_list:
                logger.debug("Found IP list: {0}".format(auth_ip_list))
                rem_ip = cherrypy.request.headers.get('Remote-Addr', None)
                logger.debug("Request from IP: {0}".format(rem_ip))
                if not rem_ip in auth_ip_list:
                    logger.error("Blocked IP: {0}".format(rem_ip))
                    cherrypy.response.status = 403
                    return {
                        'status': cherrypy.response.status,
                        'return': "Bad IP",
                    }
    request = cherrypy.serving.request
    cherrypy.response.headers['Access-Control-Allow-Origin'] = '*'


def salt_auth_tool():
    '''
    Redirect all unauthenticated requests to the login page
    '''
    # Redirect to the login page if the session hasn't been authed
    if not cherrypy.session.has_key('token'):
        raise cherrypy.InternalRedirect('/login')

    # Session is authenticated; inform caches
    cherrypy.response.headers['Cache-Control'] = 'private'

# Be conservative in what you send
# Maps Content-Type to serialization functions; this is a tuple of tuples to
# preserve order of preference.
ct_out_map = (
    ('application/json', json.dumps),
    ('application/x-yaml', functools.partial(
        yaml.safe_dump, default_flow_style=False)),
)

def hypermedia_handler(*args, **kwargs):
    '''
    Determine the best output format based on the Accept header, execute the
    regular handler, and transform the output to the request content type (even
    if it's an error).

    :param args: Pass args through to the main handler
    :param kwargs: Pass kwargs through to the main handler
    '''
    # Execute the real handler. Handle or pass-through any errors we know how
    # to handle (auth & HTTP errors). Reformat any errors we don't know how to
    # handle as a data structure.
    try:
        cherrypy.response.processors = dict(ct_out_map) # handlers may modify this
        ret = cherrypy.serving.request._hypermedia_inner_handler(*args, **kwargs)
    except salt.exceptions.EauthAuthenticationError:
        raise cherrypy.InternalRedirect('/login')
    except cherrypy.CherryPyException:
        raise
    except Exception as exc:
        import traceback

        logger.debug("Error while processing request for: %s",
                cherrypy.request.path_info,
                exc_info=True)

        cherrypy.response.status = 500

        ret = {
            'status': cherrypy.response.status,
            'return': '{0}'.format(traceback.format_exc(exc))
                    if cherrypy.config['debug']
                    else "An unexpected error occurred"}

    # Raises 406 if requested content-type is not supported
    best = cherrypy.lib.cptools.accept([i for (i, _) in ct_out_map])

    # Transform the output from the handler into the requested output format
    cherrypy.response.headers['Content-Type'] = best
    out = cherrypy.response.processors[best]
    return out(ret)


def hypermedia_out():
    '''
    Determine the best handler for the requested content type

    Wrap the normal handler and transform the output from that handler into the
    requested content type
    '''
    request = cherrypy.serving.request
    request._hypermedia_inner_handler = request.handler
    request.handler = hypermedia_handler

    cherrypy.response.headers['Access-Control-Allow-Origin'] = '*'


@functools.wraps
def process_request_body(fn):
    '''
    A decorator to skip a processor function if process_request_body is False
    '''
    def wrapped(*args, **kwargs):
        if cherrypy.request.process_request_body != False:
            fn(*args, **kwargs)
    return wrapped


def urlencoded_processor(entity):
    '''
    Accept x-www-form-urlencoded data (run through CherryPy's formatter)
    and reformat it into a Low State data structure.

    Since we can't easily represent complicated data structures with
    key-value pairs, any more complicated requirements (e.g. compound
    commands) must instead be delivered via JSON or YAML.

    For example::

        curl -si localhost:8000 -d client=local -d tgt='*' \\
                -d fun='test.kwarg' -d arg='one=1' -d arg='two=2'

    :param entity: raw POST data
    '''
    # First call out to CherryPy's default processor
    cherrypy._cpreqbody.process_urlencoded(entity)
    cherrypy.serving.request.unserialized_data = entity.params


@process_request_body
def json_processor(entity):
    '''
    Unserialize raw POST data in JSON format to a Python data structure.

    :param entity: raw POST data
    '''
    body = entity.fp.read()
    try:
        cherrypy.serving.request.unserialized_data = json.loads(body)
    except ValueError:
        raise cherrypy.HTTPError(400, 'Invalid JSON document')


@process_request_body
def yaml_processor(entity):
    '''
    Unserialize raw POST data in YAML format to a Python data structure.

    :param entity: raw POST data
    '''
    body = entity.fp.read()
    try:
        cherrypy.serving.request.unserialized_data = yaml.safe_load(body)
    except ValueError:
        raise cherrypy.HTTPError(400, 'Invalid YAML document')


@process_request_body
def text_processor(entity):
    '''
    Attempt to unserialize plain text as JSON

    Some large services still send JSON with a text/plain Content-Type. Those
    services are bad and should feel bad.

    :param entity: raw POST data
    '''
    body = entity.fp.read()
    try:
        cherrypy.serving.request.unserialized_data = json.loads(body)
    except ValueError:
        cherrypy.serving.request.unserialized_data = body


def hypermedia_in():
    '''
    Unserialize POST/PUT data of a specified Content-Type.

    The following custom processors all are intended to format Low State data
    and will place that data structure into the request object.

    :raises HTTPError: if the request contains a Content-Type that we do not
        have a processor for
    '''
    # Be liberal in what you accept
    ct_in_map = {
        'application/x-www-form-urlencoded': urlencoded_processor,
        'application/json': json_processor,
        'application/x-yaml': yaml_processor,
        'text/yaml': yaml_processor,
        'text/plain': text_processor,
    }

    # Do not process the body for POST requests that have specified no content
    # or have not specified Content-Length
    if (cherrypy.request.method.upper() == 'POST'
            and cherrypy.request.headers.get('Content-Length', '0') == '0'):
        cherrypy.request.process_request_body = False

    cherrypy.request.body.processors.clear()
    cherrypy.request.body.default_proc = cherrypy.HTTPError(
            406, 'Content type not supported')
    cherrypy.request.body.processors = ct_in_map


def lowdata_fmt():
    '''
    Validate and format lowdata from incoming unserialized request data

    This tool requires that the hypermedia_in tool has already been run.
    '''
    if cherrypy.request.method.upper() != 'POST':
        return

    # TODO: call lowdata validation routines from here

    data = cherrypy.request.unserialized_data

    if cherrypy.request.headers['Content-Type'] == 'application/x-www-form-urlencoded':
        # Make the 'arg' param a list if not already
        if 'arg' in data and not isinstance(data['arg'], list):
            data['arg'] = [data['arg']]

        # Finally, make a Low State and put it in request
        cherrypy.request.lowstate = [data]
    else:
        cherrypy.serving.request.lowstate = data


cherrypy.tools.salt_token = cherrypy.Tool('on_start_resource',
        salt_token_tool, priority=55)
cherrypy.tools.salt_auth = cherrypy.Tool('before_request_body',
        salt_auth_tool, priority=60)
cherrypy.tools.hypermedia_in = cherrypy.Tool('before_request_body',
        hypermedia_in)
cherrypy.tools.lowdata_fmt = cherrypy.Tool('before_handler',
        lowdata_fmt, priority=40)
cherrypy.tools.hypermedia_out = cherrypy.Tool('before_handler',
        hypermedia_out)
cherrypy.tools.salt_ip_verify = cherrypy.Tool('before_handler',
        salt_ip_verify_tool)


###############################################################################


class LowDataAdapter(object):
    '''
    The primary entry point to Salt's REST API

    '''
    exposed = True

    _cp_config = {
        'tools.sessions.on': True,
        'tools.sessions.timeout': 60 * 10, # 10 hours

        # 'tools.autovary.on': True,

        'tools.hypermedia_out.on': True,
        'tools.hypermedia_in.on': True,
        'tools.lowdata_fmt.on': True,
        'tools.salt_ip_verify.on': True,
    }

    def __init__(self):
        self.opts = cherrypy.config['saltopts']
        self.api = saltapi.APIClient(self.opts)

    def exec_lowstate(self, client=None, token=None):
        '''
        Pull a Low State data structure from request and execute the low-data
        chunks through Salt. The low-data chunks will be updated to include the
        authorization token for the current session.
        '''
        lowstate = cherrypy.request.lowstate

        # Release the session lock before executing any potentially
        # long-running Salt commands. This allows different threads to execute
        # Salt commands concurrently without blocking.
        cherrypy.session.release_lock()

        # if the lowstate loaded isn't a list, lets notify the client
        if type(lowstate) != list:
            raise cherrypy.HTTPError(400, 'Lowstates must be a list')

        # Make any requested additions or modifications to each lowstate, then
        # execute each one and yield the result.
        for chunk in lowstate:
            if token:
                chunk['token'] = token

            if client:
                chunk['client'] = client

            # Make any 'arg' params a list if not already.
            # This is largely to fix a deficiency in the urlencoded format.
            if 'arg' in chunk and not isinstance(chunk['arg'], list):
                chunk['arg'] = [chunk['arg']]

            ret = self.api.run(chunk)

            # Sometimes Salt gives us a return and sometimes an iterator
            if isinstance(ret, collections.Iterator):
                for i in ret:
                    yield i
            else:
                yield ret

    def GET(self):
        '''
        An explanation of the API with links of where to go next

        .. http:get:: /

            :reqheader Accept: |req_accept|

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            % curl -i localhost:8000

        .. code-block:: http

            GET / HTTP/1.1
            Host: localhost:8000
            Accept: application/json

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Type: application/json
        '''
        import inspect

        # Grab all available client interfaces
        clients = [name for name, _ in inspect.getmembers(saltapi.APIClient,
            predicate=inspect.ismethod) if not name.startswith('__')]
        clients.remove('run') # run method calls client interfaces

        return {
            'return': "Welcome",
            'clients': clients,
        }

    @cherrypy.tools.salt_token()
    @cherrypy.tools.salt_auth()
    def POST(self, **kwargs):
        '''
        Send one or more Salt commands in the request body

        .. http:post:: /

            :reqheader X-Auth-Token: |req_token|
            :reqheader Accept: |req_accept|
            :reqheader Content-Type: |req_ct|

            :resheader Content-Type: |res_ct|

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

            :term:`lowstate` data describing Salt commands must be sent in the
            request body.

        **Example request**::

            % curl -si https://localhost:8000 \\
                    -H "Accept: application/x-yaml" \\
                    -H "X-Auth-Token: d40d1e1e" \\
                    -d client=local \\
                    -d tgt='*' \\
                    -d fun='test.ping' \\
                    -d arg

        .. code-block:: http

            POST / HTTP/1.1
            Host: localhost:8000
            Accept: application/x-yaml
            X-Auth-Token: d40d1e1e
            Content-Length: 36
            Content-Type: application/x-www-form-urlencoded

            fun=test.ping&arg&client=local&tgt=*

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Length: 200
            Allow: GET, HEAD, POST
            Content-Type: application/x-yaml

            return:
            - ms-0: true
                ms-1: true
                ms-2: true
                ms-3: true
                ms-4: true
        '''
        return {
            'return': list(self.exec_lowstate(
                token=cherrypy.session.get('token')))
        }


class Minions(LowDataAdapter):
    _cp_config = dict(LowDataAdapter._cp_config, **{
        'tools.salt_token.on': True,
        'tools.salt_auth.on': True,
    })

    def GET(self, mid=None):
        '''
        A convenience URL for getting lists of minions or getting minion
        details

        .. http:get:: /minions/(mid)

            :reqheader X-Auth-Token: |req_token|
            :reqheader Accept: |req_accept|

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            % curl -i localhost:8000/minions/ms-3

        .. code-block:: http

            GET /minions/ms-3 HTTP/1.1
            Host: localhost:8000
            Accept: application/x-yaml

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Length: 129005
            Content-Type: application/x-yaml

            return:
            - ms-3:
                grains.items:
                    ...
        '''
        cherrypy.request.lowstate = [{
            'client': 'local', 'tgt': mid or '*', 'fun': 'grains.items',
        }]
        return {
            'return': list(self.exec_lowstate(
                token=cherrypy.session.get('token'))),
        }

    def POST(self, **kwargs):
        '''
        Start an execution command and immediately return the job id

        .. http:post:: /minions

            :reqheader X-Auth-Token: |req_token|
            :reqheader Accept: |req_accept|
            :reqheader Content-Type: |req_ct|

            :resheader Content-Type: |res_ct|

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

            :term:`lowstate` data describing Salt commands must be sent in the
            request body. The ``client`` option will be set to
            :py:meth:`~salt.client.LocalClient.local_async`.

        **Example request**::

            % curl -sSi localhost:8000/minions \\
                -H "Accept: application/x-yaml" \\
                -d tgt='*' \\
                -d fun='status.diskusage'

        .. code-block:: http

            POST /minions HTTP/1.1
            Host: localhost:8000
            Accept: application/x-yaml
            Content-Length: 26
            Content-Type: application/x-www-form-urlencoded

            tgt=*&fun=status.diskusage

        **Example response**:

        .. code-block:: http

            HTTP/1.1 202 Accepted
            Content-Length: 86
            Content-Type: application/x-yaml

            return:
            - jid: '20130603122505459265'
                minions: [ms-4, ms-3, ms-2, ms-1, ms-0]
            _links:
                jobs:
                - href: /jobs/20130603122505459265
        '''
        job_data = list(self.exec_lowstate(client='local_async',
            token=cherrypy.session.get('token')))

        cherrypy.response.status = 202
        return {
            'return': job_data,
            '_links': {
                'jobs': [{'href': '/jobs/{0}'.format(i['jid'])}
                    for i in job_data if i],
            },
        }


class Jobs(LowDataAdapter):
    _cp_config = dict(LowDataAdapter._cp_config, **{
        'tools.salt_token.on': True,
        'tools.salt_auth.on': True,
    })

    def GET(self, jid=None):
        '''
        A convenience URL for getting lists of previously run jobs or getting
        the return from a single job

        .. http:get:: /jobs/(jid)

            List jobs or show a single job from the job cache.

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            % curl -i localhost:8000/jobs

        .. code-block:: http

            GET /jobs HTTP/1.1
            Host: localhost:8000
            Accept: application/x-yaml

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Length: 165
            Content-Type: application/x-yaml

            return:
            - '20121130104633606931':
                Arguments:
                - '3'
                Function: test.fib
                Start Time: 2012, Nov 30 10:46:33.606931
                Target: jerry
                Target-type: glob

        **Example request**::

            % curl -i localhost:8000/jobs/20121130104633606931

        .. code-block:: http

            GET /jobs/20121130104633606931 HTTP/1.1
            Host: localhost:8000
            Accept: application/x-yaml

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Length: 73
            Content-Type: application/x-yaml

            info:
            - Arguments:
                - '3'
                Function: test.fib
                Minions:
                - jerry
                Start Time: 2012, Nov 30 10:46:33.606931
                Target: '*'
                Target-type: glob
                User: saltdev
                jid: '20121130104633606931'
            return:
            - jerry:
                - - 0
                - 1
                - 1
                - 2
                - 6.9141387939453125e-06
        '''
        lowstate = [{
            'client': 'runner',
            'fun': 'jobs.lookup_jid' if jid else 'jobs.list_jobs',
            'jid': jid,
        }]

        if jid:
            lowstate.append({
                'client': 'runner',
                'fun': 'jobs.list_job',
                'jid': jid,
            })

        cherrypy.request.lowstate = lowstate
        job_ret_info = list(self.exec_lowstate(
            token=cherrypy.session.get('token')))

        ret = {}
        if jid:
            job_ret, job_info = job_ret_info
            ret['info'] = [job_info]
        else:
            job_ret = job_ret_info[0]

        ret['return'] = [job_ret]
        return ret


class Login(LowDataAdapter):
    '''
    Log in to recieve a session token

    :ref:`Authentication information <rest_cherrypy-auth>`.
    '''

    def __init__(self, *args, **kwargs):
        super(Login, self).__init__(*args, **kwargs)

        self.auth = salt.auth.Resolver(self.opts)

    def GET(self):
        '''
        Present the login interface

        .. http:get:: /login

            An explanation of how to log in.

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            % curl -i localhost:8000/login

        .. code-block:: http

            GET /login HTTP/1.1
            Host: localhost:8000
            Accept: text/html

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Type: text/html
        '''
        cherrypy.response.headers['WWW-Authenticate'] = 'Session'

        return {
            'status': cherrypy.response.status,
            'return': "Please log in",
        }

    def POST(self, **kwargs):
        '''
        :ref:`Authenticate  <rest_cherrypy-auth>` against Salt's eauth system

        .. versionchanged:: 0.8.0
            No longer returns a 302 redirect on success.

        .. versionchanged:: 0.8.1
            Returns 401 on authentication failure

        .. http:post:: /login

            :reqheader X-Auth-Token: |req_token|
            :reqheader Accept: |req_accept|
            :reqheader Content-Type: |req_ct|

            :form eauth: the eauth backend configured for the user
            :form username: username
            :form password: password

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            % curl -si localhost:8000/login \\
                    -H "Accept: application/json" \\
                    -d username='saltuser' \\
                    -d password='saltpass' \\
                    -d eauth='pam'

        .. code-block:: http

            POST / HTTP/1.1
            Host: localhost:8000
            Content-Length: 42
            Content-Type: application/x-www-form-urlencoded
            Accept: application/json

            username=saltuser&password=saltpass&eauth=pam

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Type: application/json
            Content-Length: 206
            X-Auth-Token: 6d1b722e
            Set-Cookie: session_id=6d1b722e; expires=Sat, 17 Nov 2012 03:23:52 GMT; Path=/

            {"return": {
                "token": "6d1b722e",
                "start": 1363805943.776223,
                "expire": 1363849143.776224,
                "user": "saltuser",
                "eauth": "pam",
                "perms": [
                    "grains.*",
                    "status.*",
                    "sys.*",
                    "test.*"
                ]
            }}
        '''
        # the urlencoded_processor will wrap this in a list
        if isinstance(cherrypy.serving.request.lowstate, list):
            creds = cherrypy.serving.request.lowstate[0]
        else:
            creds = cherrypy.serving.request.lowstate

        token = self.auth.mk_token(creds)
        if not 'token' in token:
            raise cherrypy.HTTPError(401,
                    'Could not authenticate using provided credentials')

        cherrypy.response.headers['X-Auth-Token'] = cherrypy.session.id
        cherrypy.session['token'] = token['token']
        cherrypy.session['timeout'] = (token['expire'] - token['start']) / 60

        # Grab eauth config for the current backend for the current user
        try:
            perms = self.opts['external_auth'][token['eauth']][token['name']]
        except (AttributeError, IndexError):
            logger.debug("Configuration for external_auth malformed for "\
                "eauth '{0}', and user '{1}'."
                .format(token.get('eauth'), token.get('name')), exc_info=True)
            raise cherrypy.HTTPError(500,
                'Configuration for external_auth could not be read.')

        return {'return': [{
            'token': cherrypy.session.id,
            'expire': token['expire'],
            'start': token['start'],
            'user': token['name'],
            'eauth': token['eauth'],
            'perms': perms,
        }]}


class Logout(LowDataAdapter):
    _cp_config = dict(LowDataAdapter._cp_config, **{
        'tools.salt_token.on': True,
        'tools.salt_auth.on': True,
    })

    def POST(self):
        '''
        Destroy the currently active session and expire the session cookie

        .. versionadded:: 0.8.0
        '''
        cherrypy.lib.sessions.expire() # set client-side to expire
        cherrypy.session.regenerate() # replace server-side with new

        return {'return': "Your token has been cleared"}


class Run(LowDataAdapter):
    _cp_config = dict(LowDataAdapter._cp_config, **{
        'tools.sessions.on': False,
    })

    def POST(self, **kwargs):
        '''
        Run commands bypassing the :ref:`normal session handling
        <rest_cherrypy-auth>`

        .. versionadded:: 0.8.0

        .. http:post:: /run

            This entry point is primarily for "one-off" commands. Each request
            must pass full Salt authentication credentials. Otherwise this URL
            is identical to the :py:meth:`root URL (/) <LowDataAdapter.POST>`.

            :term:`lowstate` data describing Salt commands must be sent in the
            request body.

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            % curl -sS localhost:8000/run \\
                -H 'Accept: application/x-yaml' \\
                -d client='local' \\
                -d tgt='*' \\
                -d fun='test.ping' \\
                -d username='saltdev' \\
                -d password='saltdev' \\
                -d eauth='pam'

        .. code-block:: http

            POST /run HTTP/1.1
            Host: localhost:8000
            Accept: application/x-yaml
            Content-Length: 75
            Content-Type: application/x-www-form-urlencoded

            client=local&tgt=*&fun=test.ping&username=saltdev&password=saltdev&eauth=pam

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Length: 73
            Content-Type: application/x-yaml

            return:
            - ms-0: true
                ms-1: true
                ms-2: true
                ms-3: true
                ms-4: true
        '''
        return {
            'return': list(self.exec_lowstate()),
        }


class Events(object):
    '''
    Expose the Salt event bus

    The event bus on the Salt master exposes a large variety of things, notably
    when executions are started on the master and also when minions ultimately
    return their results. This URL provides a real-time window into a running
    Salt infrastructure.

    .. seealso:: :ref:`events`
    '''
    exposed = True

    _cp_config = dict(LowDataAdapter._cp_config, **{
        'response.stream': True,
        'tools.encode.encoding': 'utf-8',

        # Auth handled manually below
        'tools.salt_token.on': True,
        'tools.salt_auth.on': False,

        'tools.hypermedia_in.on': False,
        'tools.hypermedia_out.on': False,
    })

    def __init__(self):
        self.opts = cherrypy.config['saltopts']
        self.auth = salt.auth.LoadAuth(self.opts)

    def GET(self, token=None):
        '''
        An HTTP stream of the Salt master event bus

        This stream is formatted per the Server Sent Events (SSE) spec. Each
        event is formatted as JSON.

        .. versionadded:: 0.8.3

        Browser clients currently lack Cross-origin resource sharing (CORS)
        support for the ``EventSource()`` API. Cross-domain requests from a
        browser may instead pass the :mailheader:`X-Auth-Token` value as an URL
        parameter::

            % curl -NsS localhost:8000/events/6d1b722e

        .. http:get:: /events

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            % curl -NsS localhost:8000/events

        .. code-block:: http

            GET /events HTTP/1.1
            Host: localhost:8000

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Connection: keep-alive
            Cache-Control: no-cache
            Content-Type: text/event-stream;charset=utf-8

            retry: 400
            data: {'tag': '', 'data': {'minions': ['ms-4', 'ms-3', 'ms-2', 'ms-1', 'ms-0']}}

            data: {'tag': '20130802115730568475', 'data': {'jid': '20130802115730568475', 'return': True, 'retcode': 0, 'success': True, 'cmd': '_return', 'fun': 'test.ping', 'id': 'ms-1'}}

    The event stream can be easily consumed via JavaScript:

    .. code-block:: javascript

        # Note, you must be authenticated!
        var source = new EventSource('/events');
        source.onopen = function() { console.debug('opening') };
        source.onerror = function(e) { console.debug('error!', e) };
        source.onmessage = function(e) { console.debug(e.data) };

    It is also possible to consume the stream via the shell.

    Records are separated by blank lines; the ``data:`` and ``tag:``
    prefixes will need to be removed manually before attempting to
    unserialize the JSON.

    curl's ``-N`` flag turns off input buffering which is required to
    process the stream incrementally.

    Here is a basic example of printing each event as it comes in:

    .. code-block:: bash

        % curl -NsS localhost:8000/events |\\
                while IFS= read -r line ; do
                    echo $line
                done

    Here is an example of using awk to filter events based on tag:

    .. code-block:: bash

        % curl -NsS localhost:8000/events |\\
                awk '
                    BEGIN { RS=""; FS="\\n" }
                    $1 ~ /^tag: salt\/job\/[0-9]+\/new$/ { print $0 }
                '
        tag: salt/job/20140112010149808995/new
        data: {"tag": "salt/job/20140112010149808995/new", "data": {"tgt_type": "glob", "jid": "20140112010149808995", "tgt": "jerry", "_stamp": "2014-01-12_01:01:49.809617", "user": "shouse", "arg": [], "fun": "test.ping", "minions": ["jerry"]}}
        tag: 20140112010149808995
        data: {"tag": "20140112010149808995", "data": {"fun_args": [], "jid": "20140112010149808995", "return": true, "retcode": 0, "success": true, "cmd": "_return", "_stamp": "2014-01-12_01:01:49.819316", "fun": "test.ping", "id": "jerry"}}
        '''
        # Pulling the session token from an URL param is a workaround for
        # browsers not supporting CORS in the EventSource API.
        if token:
            orig_sesion, _ = cherrypy.session.cache.get(token, ({}, None))
            salt_token = orig_sesion.get('token')
        else:
            salt_token = cherrypy.session.get('token')

        # Manually verify the token
        if not salt_token or not self.auth.get_tok(salt_token):
            raise cherrypy.InternalRedirect('/login')

        # Release the session lock before starting the long-running response
        cherrypy.session.release_lock()

        cherrypy.response.headers['Content-Type'] = 'text/event-stream'
        cherrypy.response.headers['Cache-Control'] = 'no-cache'
        cherrypy.response.headers['Connection'] = 'keep-alive'

        def listen():
            event = salt.utils.event.get_event('master', opts=self.opts)
            stream = event.iter_events(full=True)

            yield u'retry: {0}\n'.format(400)

            while True:
                data = stream.next()
                yield u'tag: {0}\n'.format(data.get('tag', ''))
                yield u'data: {0}\n\n'.format(json.dumps(data))

        return listen()


class WebsocketEndpoint(object):
    '''
    Open a WebSocket connection to Salt's event bus

    The event bus on the Salt master exposes a large variety of things, notably
    when executions are started on the master and also when minions ultimately
    return their results. This URL provides a real-time window into a running
    Salt infrastructure. Uses websocket as the transport mechanism.

    .. seealso:: :ref:`events`
    '''
    exposed = True

    _cp_config = dict(LowDataAdapter._cp_config, **{
        'response.stream': True,
        'tools.encode.encoding': 'utf-8',

        # Auth handled manually below
        'tools.salt_token.on': True,
        'tools.salt_auth.on': False,

        'tools.hypermedia_in.on': False,
        'tools.hypermedia_out.on': False,
        'tools.websocket.on': True,
        'tools.websocket.handler_cls': websockets.SynchronizingWebsocket,
    })

    def __init__(self):
        self.opts = cherrypy.config['saltopts']
        self.auth = salt.auth.LoadAuth(self.opts)

    def GET(self, token=None, **kwargs):
        '''
        Return a websocket connection of Salt's event stream

        .. versionadded:: 0.8.6

        .. http:get:: /ws/(token)

            :query format_events: The event stream will undergo server-side
                formatting if the ``format_events`` URL parameter is included
                in the request. This can be useful to avoid formatting on the
                client-side::

                    curl -NsS <...snip...> localhost:8000/ws?format_events

            :reqheader X-Auth-Token: an authentication token from
                :py:class:`~Login`.

            :status 101: switching to the websockets protocol
            :status 401: |401|
            :status 406: |406|

        **Example request**::

            curl -NsS \\
                -H 'X-Auth-Token: ffedf49d' \\
                -H 'Host: localhost:8000' \\
                -H 'Connection: Upgrade' \\
                -H 'Upgrade: websocket' \\
                -H 'Origin: http://localhost:8000' \\
                -H 'Sec-WebSocket-Version: 13' \\
                -H 'Sec-WebSocket-Key: '"$(echo -n $RANDOM | base64)" \\
                localhost:8000/ws

        .. code-block:: http

            GET /ws HTTP/1.1
            Connection: Upgrade
            Upgrade: websocket
            Host: localhost:8000
            Origin: http://localhost:8000
            Sec-WebSocket-Version: 13
            Sec-WebSocket-Key: s65VsgHigh7v/Jcf4nXHnA==
            X-Auth-Token: ffedf49d

        **Example response**:

        .. code-block:: http

            HTTP/1.1 101 Switching Protocols
            Upgrade: websocket
            Connection: Upgrade
            Sec-WebSocket-Accept: mWZjBV9FCglzn1rIKJAxrTFlnJE=
            Sec-WebSocket-Version: 13

        An authentication token **may optionally** be passed as part of the URL
        for browsers that cannot be configured to send the authentication
        header or cookie::

                curl -NsS <...snip...> localhost:8000/ws/ffedf49d

        The event stream can be easily consumed via JavaScript:

        .. code-block:: javascript

            // Note, you must be authenticated!
            var source = new Websocket('ws://localhost:8000/ws/d0ce6c1a');
            source.onerror = function(e) { console.debug('error!', e); };
            source.onmessage = function(e) { console.debug(e.data); };

            source.send('websocket client ready')

            source.close();

        Or via Python, using the Python module
        `websocket-client <https://pypi.python.org/pypi/websocket-client/>`_ for example.

        .. code-block:: python

            # Note, you must be authenticated!

            from websocket import create_connection

            ws = create_connection('ws://localhost:8000/ws/d0ce6c1a')
            ws.send('websocket client ready')

            # Look at https://pypi.python.org/pypi/websocket-client/ for more examples.
            while listening_to_events:
                print ws.recv()

            ws.close()

        Above examples show how to establish a websocket connection to Salt and
        activating real time updates from Salt's event stream by signaling
        ``websocket client ready``.
        '''
        # Pulling the session token from an URL param is a workaround for
        # browsers not supporting CORS in the EventSource API.
        if token:
            orig_sesion, _ = cherrypy.session.cache.get(token, ({}, None))
            salt_token = orig_sesion.get('token')
        else:
            salt_token = cherrypy.session.get('token')

        # Manually verify the token
        if not salt_token or not self.auth.get_tok(salt_token):
            raise cherrypy.HTTPError(401)

        # Release the session lock before starting the long-running response
        cherrypy.session.release_lock()

        '''
        A handler is the server side end of the websocket connection.
        Each request spawns a new instance of this handler
        '''
        handler = cherrypy.request.ws_handler

        def event_stream(handler, pipe):
            pipe.recv()  # blocks until send is called on the parent end of this pipe.

            event = salt.utils.event.get_event('master', opts=self.opts)
            stream = event.iter_events(full=True)
            SaltInfo = event_processor.SaltInfo(handler)
            while True:
                data = stream.next()
                if data:
                    try: #work around try to decode catch unicode errors
                        if 'format_events' in kwargs:
                            SaltInfo.process(data, salt_token, self.opts)
                        else:
                            handler.send('data: {0}\n\n'.format(json.dumps(data)), False)
                    except UnicodeDecodeError as ex:
                        logger.error("Error: Salt event has non UTF-8 data:\n{0}".format(data))
                time.sleep(0.1)

        parent_pipe, child_pipe = Pipe()
        handler.pipe = parent_pipe
        handler.opts = self.opts
        # Process to handle async push to a client.
        # Each GET request causes a process to be kicked off.
        proc = Process(target=event_stream, args=(handler,child_pipe))
        proc.start()


class Webhook(object):
    '''
    A generic web hook entry point that fires an event on Salt's event bus

    External services can POST data to this URL to trigger an event in Salt.
    For example, Amazon SNS, Jenkins-CI or Travis-CI, or GitHub web hooks.

    .. note:: Be mindful of security

        Salt's Reactor can run any code. A Reactor SLS that responds to a hook
        event is responsible for validating that the event came from a trusted
        source and contains valid data.

        **This is a generic interface and securing it is up to you!**

        This URL requires authentication however not all external services can
        be configured to authenticate. For this reason authentication can be
        selectively disabled for this URL. Follow best practices -- always use
        SSL, pass a secret key, configure the firewall to only allow traffic
        from a known source, etc.

    The event data is taken from the request body. The
    :mailheader:`Content-Type` header is respected for the payload.

    The event tag is prefixed with ``salt/netapi/hook`` and the URL path is
    appended to the end. For example, a ``POST`` request sent to
    ``/hook/mycompany/myapp/mydata`` will produce a Salt event with the tag
    ``salt/netapi/hook/mycompany/myapp/mydata``.

    The following is an example ``.travis.yml`` file to send notifications to
    Salt of successful test runs:

    .. code-block:: yaml

        language: python
        script: python -m unittest tests
        after_success:
            - 'curl -sS http://saltapi-url.example.com:8000/hook/travis/build/success -d branch="${TRAVIS_BRANCH}" -d commit="${TRAVIS_COMMIT}"'

    .. seealso:: :ref:`events`, :ref:`reactor`
    '''
    exposed = True
    tag_base = ['salt', 'netapi', 'hook']

    _cp_config = dict(LowDataAdapter._cp_config, **{
        # Don't do any lowdata processing on the POST data
        'tools.lowdata_fmt.on': True,

        # Auth can be overridden in __init__().
        'tools.salt_token.on': True,
        'tools.salt_auth.on': True,
    })

    def __init__(self):
        self.opts = cherrypy.config['saltopts']
        self.event = salt.utils.event.get_event('master', opts=self.opts)

        if cherrypy.config['apiopts'].get('webhook_disable_auth'):
            self._cp_config['tools.salt_token.on'] = False
            self._cp_config['tools.salt_auth.on'] = False

    def POST(self, *args, **kwargs):
        '''
        Fire an event in Salt with a custom event tag and data

        .. versionadded:: 0.8.4

        .. http:post:: /hook

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|
            :status 413: request body is too large

        **Example request**::

            % curl -sS localhost:8000/hook -d foo='Foo!' -d bar='Bar!'

        .. code-block:: http

            POST /hook HTTP/1.1
            Host: localhost:8000
            Content-Length: 16
            Content-Type: application/x-www-form-urlencoded

            foo=Foo&bar=Bar!

        **Example response**:

        .. code-block:: http

            HTTP/1.1 200 OK
            Content-Length: 14
            Content-Type: application/json

            {"success": true}

        As a practical example, an internal continuous-integration build
        server could send an HTTP POST request to the URL
        ``http://localhost:8000/hook/mycompany/build/success`` which contains
        the result of a build and the SHA of the version that was built as
        JSON. That would then produce the following event in Salt that could be
        used to kick off a deployment via Salt's Reactor::

            Event fired at Fri Feb 14 17:40:11 2014
            *************************
            Tag: salt/netapi/hook/mycompany/build/success
            Data:
            {'_stamp': '2014-02-14_17:40:11.440996',
                'headers': {
                    'X-My-Secret-Key': 'F0fAgoQjIT@W',
                    'Content-Length': '37',
                    'Content-Type': 'application/json',
                    'Host': 'localhost:8000',
                    'Remote-Addr': '127.0.0.1'},
                'post': {'revision': 'aa22a3c4b2e7', 'result': True}}

        Salt's Reactor could listen for the event:

        .. code-block:: yaml

            reactor:
              - 'salt/netapi/hook/mycompany/build/*':
                - /srv/reactor/react_ci_builds.sls

        And finally deploy the new build:

        .. code-block:: yaml

            {% set secret_key = data.get('headers', {}).get('X-My-Secret-Key') %}
            {% set build = data.get('post', {}) %}

            {% if secret_key == 'F0fAgoQjIT@W' and build.result == True %}
            deploy_my_app:
              cmd.state.sls:
                - tgt: 'application*'
                - arg:
                  - myapp.deploy
                - kwarg:
                    pillar:
                      revision: {{ revision }}
            {% endif %}
        '''
        tag = '/'.join(itertools.chain(self.tag_base, args))
        data = cherrypy.serving.request.unserialized_data
        headers = dict(cherrypy.request.headers)

        ret = self.event.fire_event({
            'post': data,
            'headers': headers,
        }, tag)
        return {'success': ret}


class Stats(object):
    '''
    Expose statistics on the running CherryPy server
    '''
    exposed = True

    _cp_config = dict(LowDataAdapter._cp_config, **{
        'tools.salt_token.on': True,
        'tools.salt_auth.on': True,
    })

    def GET(self):
        '''
        Return a dump of statistics collected from the CherryPy server

        .. http:get:: /stats

            :reqheader X-Auth-Token: |req_token|
            :reqheader Accept: |req_accept|

            :resheader Content-Type: |res_ct|

            :status 200: |200|
            :status 401: |401|
            :status 406: |406|
        '''
        if hasattr(logging, 'statistics'):
            return cpstats.extrapolate_statistics(logging.statistics)

        return {}


class App(object):
    exposed = True
    def GET(self, *args):
        '''
        Serve a single static file ignoring the remaining path

        This is useful in combination with a browser-based app using the HTML5
        history API.

        .. http::get:: /app

            :reqheader X-Auth-Token: |req_token|

            :status 200: |200|
            :status 401: |401|
        '''
        apiopts = cherrypy.config['apiopts']
        return cherrypy.lib.static.serve_file(apiopts['app'])


class API(object):
    '''
    Collect configuration and URL map for building the CherryPy app
    '''
    url_map = {
        'index': LowDataAdapter,
        'login': Login,
        'logout': Logout,
        'minions': Minions,
        'run': Run,
        'jobs': Jobs,
        'events': Events,
        'stats': Stats,
    }

    def _setattr_url_map(self):
        for url, cls in self.url_map.items():
            setattr(self, url, cls())

    def _update_url_map(self):
        '''
        Assemble any dynamic or configurable URLs
        '''
        if HAS_WEBSOCKETS:
            self.url_map.update({
                'ws': WebsocketEndpoint,
            })

        # Allow the Webhook URL to be overridden from the conf.
        self.url_map.update({
            self.apiopts.get('webhook_url', 'hook').lstrip('/'): Webhook,
        })

        # Enable the single-page JS app URL.
        if 'app' in self.apiopts:
            self.url_map.update({
                self.apiopts.get('app_path', 'app').lstrip('/'): App,
            })

    def __init__(self):
        self.opts = cherrypy.config['saltopts']
        self.apiopts = cherrypy.config['apiopts']

        self._update_url_map()
        self._setattr_url_map()

    def get_conf(self):
        '''
        Combine the CherryPy configuration with the rest_cherrypy config values
        pulled from the master config and return the CherryPy configuration
        '''
        conf = {
            'global': {
                'server.socket_host': self.apiopts.get('host', '0.0.0.0'),
                'server.socket_port': self.apiopts.get('port', 8000),
                'server.thread_pool': self.apiopts.get('thread_pool', 100),
                'server.socket_queue_size': self.apiopts.get('queue_size', 30),
                'max_request_body_size': self.apiopts.get('max_request_body_size', 1048576),
                'debug': self.apiopts.get('debug', False),
            },
            '/': {
                'request.dispatch': cherrypy.dispatch.MethodDispatcher(),

                'tools.trailing_slash.on': True,
                'tools.gzip.on': True,

                'tools.cpstats.on': self.apiopts.get('collect_stats', False),
            },
        }

        if self.apiopts.get('debug', False) == False:
            conf['global']['environment'] = 'production'

        # Serve static media if the directory has been set in the configuration
        if 'static' in self.apiopts:
            conf[self.apiopts.get('static_path', '/static')] = {
                'tools.staticdir.on': True,
                'tools.staticdir.dir': self.apiopts['static'],
            }

        # Add to global config
        cherrypy.config.update(conf['global'])

        return conf


def get_app(opts):
    '''
    Returns a WSGI app and a configuration dictionary
    '''
    apiopts = opts.get(__name__.rsplit('.', 2)[-2], {}) # rest_cherrypy opts

    # Add Salt and salt-api config options to the main CherryPy config dict
    cherrypy.config['saltopts'] = opts
    cherrypy.config['apiopts'] = apiopts

    root = API() # cherrypy app
    cpyopts = root.get_conf() # cherrypy app opts

    return root, apiopts, cpyopts
