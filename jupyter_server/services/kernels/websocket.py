"""Tornado handlers for WebSocket <-> ZMQ sockets."""
# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import json

from tornado import web
from tornado.websocket import WebSocketHandler

from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.base.websocket import WebSocketMixin

from .handlers import _kernel_id_regex

AUTH_RESOURCE = "kernels"


class KernelWebsocketHandler(WebSocketMixin, WebSocketHandler, JupyterHandler):  # type:ignore[misc]
    """The kernels websocket should connect"""

    auth_resource = AUTH_RESOURCE

    @property
    def kernel_websocket_connection_class(self):
        """The kernel websocket connection class."""
        return self.settings.get("kernel_websocket_connection_class")

    def set_default_headers(self):
        """Undo the set_default_headers in JupyterHandler

        which doesn't make sense for websockets
        """
        pass

    def get_compression_options(self):
        """Get the socket connection options."""
        return self.settings.get("websocket_compression_options", None)

    async def pre_get(self):
        """Handle a pre_get."""
        # authenticate first
        user = self.current_user
        if user is None:
            self.log.warning("Couldn't authenticate WebSocket connection")
            raise web.HTTPError(403)

        # authorize the user.
        if not self.authorizer.is_authorized(self, user, "execute", "kernels"):
            raise web.HTTPError(403)

        kernel = self.kernel_manager.get_kernel(self.kernel_id)
        self.connection = self.kernel_websocket_connection_class(
            parent=kernel, websocket_handler=self, config=self.config
        )

        if self.get_argument("session_id", None):
            self.connection.session.session = self.get_argument("session_id")
        else:
            self.log.warning("No session ID specified")
        # For backwards compatibility with older versions
        # of the websocket connection, call a prepare method if found.
        if hasattr(self.connection, "prepare"):
            await self.connection.prepare()

    async def get(self, kernel_id):
        """Handle a get request for a kernel."""
        self.kernel_id = kernel_id
        await self.pre_get()
        await super().get(kernel_id=kernel_id)

    async def open(self, kernel_id):
        """Open a kernel websocket."""
        # Wait for the kernel to emit an idle status.
        self.log.info(f"Connecting to kernel {self.kernel_id}.")
        await self.connection.connect()

    def on_message(self, ws_message):
        """Get a kernel message from the websocket and turn it into a ZMQ message."""

        # For execute_request messages then add token to environment that's being executed
        if isinstance(ws_message, str):
            try:
                message = json.loads(ws_message)
                is_execute_request = message.get('header', {}).get('msg_type') == 'execute_request'
                if is_execute_request and message.get('content', {}).get('code') is not None:
                    message['content']['code'] = f"import os\nos.environ['FORWARDED_ACCESS_TOKEN'] = " \
                                                 f"'{self.request.headers.get('X-Forwarded-Access-Token')}'\n" \
                                                 f"{message['content']['code']}"
                ws_message = json.dumps(message)
            except Exception:
                pass

        self.connection.handle_incoming_message(ws_message)

    def on_close(self):
        """Handle a socket closure."""
        self.connection.disconnect()
        self.connection = None

    def select_subprotocol(self, subprotocols):
        """Select the sub protocol for the socket."""
        preferred_protocol = self.connection.kernel_ws_protocol
        if preferred_protocol is None:
            preferred_protocol = "v1.kernel.websocket.jupyter.org"
        elif preferred_protocol == "":
            preferred_protocol = None
        selected_subprotocol = preferred_protocol if preferred_protocol in subprotocols else None
        # None is the default, "legacy" protocol
        return selected_subprotocol


default_handlers = [
    (r"/api/kernels/%s/channels" % _kernel_id_regex, KernelWebsocketHandler),
]
