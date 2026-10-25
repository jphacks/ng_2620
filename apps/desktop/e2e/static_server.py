"""Serve a built dist/ under /ng_2620/ like GitHub Pages (no /v1, no local server). usage: static_server.py <dist> <port>"""
import functools
import http.server
import sys

dist, port = sys.argv[1], int(sys.argv[2])


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        prefix = "/ng_2620"
        if path.startswith(prefix):
            path = path[len(prefix):] or "/"
        else:
            self.send_error(404)
            return dist + "/__missing__"
        return super().translate_path(path)

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=dist)).serve_forever()
