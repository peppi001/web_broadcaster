from __future__ import annotations

import ast
import errno
import unittest
from pathlib import Path


class _FakeRequestHandler:
    handle_error: OSError | None = None
    finish_error: OSError | None = None

    def handle(self) -> None:
        if self.handle_error is not None:
            raise self.handle_error

    def finish(self) -> None:
        if self.finish_error is not None:
            raise self.finish_error


class V6015ClientDisconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        module = ast.parse(cls.source)
        wanted = {
            "_IGNORABLE_CLIENT_SOCKET_ERRNOS",
            "_is_ignorable_client_socket_error",
            "_WebBroadcasterRequestHandler",
        }
        nodes = []
        for node in module.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                names = {target.id for target in getattr(node, "targets", []) if isinstance(target, ast.Name)}
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                if names & wanted:
                    nodes.append(node)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted:
                nodes.append(node)
        namespace = {
            "errno": errno,
            "WSGIRequestHandler": _FakeRequestHandler,
            "BaseException": BaseException,
            "OSError": OSError,
            "frozenset": frozenset,
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
        cls.handler_type = namespace["_WebBroadcasterRequestHandler"]
        cls.classifier = staticmethod(namespace["_is_ignorable_client_socket_error"])

    def new_handler(self):
        return self.handler_type.__new__(self.handler_type)

    def test_no_route_to_host_is_a_normal_client_disconnect(self) -> None:
        error = OSError(errno.EHOSTUNREACH, "No route to host")
        self.assertTrue(self.classifier(error))
        handler = self.new_handler()
        handler.handle_error = error
        handler.handle()

    def test_flush_disconnect_is_also_suppressed(self) -> None:
        handler = self.new_handler()
        handler.finish_error = OSError(errno.EPIPE, "Broken pipe")
        handler.finish()

    def test_unrelated_oserror_is_not_hidden(self) -> None:
        error = OSError(errno.EINVAL, "Invalid argument")
        self.assertFalse(self.classifier(error))
        handler = self.new_handler()
        handler.handle_error = error
        with self.assertRaisesRegex(OSError, "Invalid argument"):
            handler.handle()

    def test_flask_uses_the_custom_request_handler(self) -> None:
        self.assertIn("request_handler=_WebBroadcasterRequestHandler", self.source)
        self.assertIn('APP_VERSION = "6024"', self.source)


if __name__ == "__main__":
    unittest.main()
