#!/usr/bin/env python3
import os
import sys
import asyncio
import tornado.web
import tornado.gen
import tornado.concurrent
from tornado.websocket import websocket_connect

# --- 1. ПАТЧІ СУМІСНОСТІ TORNADO 6 (БЕЗ РЕКУРСІЇ) ---

if not hasattr(tornado.gen, "Task"):

    def Task(func, *args, **kwargs):
        future = tornado.concurrent.Future()

        def callback(*args):
            future.set_result(args[0] if len(args) == 1 else args)

        func(*(args + (callback,)), **kwargs)
        return future

    tornado.gen.Task = Task

if not hasattr(tornado.gen, "engine"):
    tornado.gen.engine = lambda method: method

if not hasattr(tornado.web, "asynchronous"):
    tornado.web.asynchronous = lambda method: method


# Функція конвертації yield у awaitable
def make_native_coroutine(gen_func):
    if not gen_func:
        return None

    def wrapper(*args, **kwargs):
        result = gen_func(*args, **kwargs)
        if isinstance(result, type((x for x in []))):
            return tornado.gen.convert_yielded(result)
        return result

    return wrapper


# ПРАВИЛЬНИЙ ПАТЧ: Зберігаємо оригінал, щоб уникнути рекурсії
_original_coroutine = tornado.gen.coroutine


def safe_patched_coroutine(func):
    # Викликаємо оригінал
    coro = _original_coroutine(func)
    # Огортаємо в наш конвертер
    return make_native_coroutine(coro)


tornado.gen.coroutine = safe_patched_coroutine

# Патч для виконання методів RequestHandler
original_execute = tornado.web.RequestHandler._execute


async def patched_execute(self, *args, **kwargs):
    res = original_execute(self, *args, **kwargs)
    if isinstance(res, type((x for x in []))):
        return await tornado.gen.convert_yielded(res)
    return await res


tornado.web.RequestHandler._execute = patched_execute
# --- 2. НАЛАШТУВАННЯ СЕРЕДОВИЩА ---
ROOT = os.path.dirname(os.path.realpath(__file__))
sys.path = [ROOT] + sys.path
os.environ["MOD_DEV"] = "1"
os.environ["MOD_LIVE_DIR"] = os.path.expanduser("~/.mod-drive")

from mod import webserver, protocol
from mod.host import Host
from mod.development import FakeHMI
from mod.session import SESSION

# ПАТЧІ ШЛЯХІВ
HTML_DIR = os.path.join(ROOT, "html")
webserver.HTML_DIR = HTML_DIR
webserver.DEFAULT_ICON_TEMPLATE = os.path.join(
    HTML_DIR, "resources", "templates", "pedal-default.html"
)
webserver.DEFAULT_SETTINGS_TEMPLATE = os.path.join(
    HTML_DIR, "resources", "settings.html"
)
webserver.BUNDLE_DIR = os.path.join(HTML_DIR, "resources", "bundles")
webserver.TEMPLATES_DIR = os.path.join(HTML_DIR, "resources", "templates")

# ПАТЧ СЕСІЇ (Щоб прибрати "Not Connected")
SESSION.hw_initialized = True
SESSION.ui_connected = True


async def fake_wait(*args, **kwargs):
    return True


SESSION.wait_for_hardware_if_needed = fake_wait


class MockPrefs:
    def get(self, key, default=None, *args, **kwargs):
        return default


class SlaveHost(Host):
    def __init__(self, hmi, prefs, msg_callback):
        super(SlaveHost, self).__init__(hmi, prefs, msg_callback)
        self.connected = True

    def is_jack_running(self):
        return True  # Брешемо, що Jack працює

    def send_command(self, comm):
        pass


_original_register = protocol.Protocol.register_cmd_callback


def safe_register(cls, group, cmd, callback):
    try:
        _original_register(group, cmd, callback)
    except ValueError:
        pass


protocol.Protocol.register_cmd_callback = classmethod(safe_register)


# --- 3. СИНХРОНІЗАЦІЯ З MASTER ---
async def sync_with_master(app):
    url = "ws://192.168.88.247/websocket"
    print(f"[*] Connecting to Master: {url}")
    while True:
        try:
            conn = await websocket_connect(url)
            print("[+] SYNC ACTIVE")
            while True:
                msg = await conn.read_message()
                if msg is None:
                    break
                try:
                    app.host.handle_message(msg)
                except Exception:
                    pass

                clients = getattr(webserver.GlobalWebServerState, "ws_clients", [])
                for client in clients:
                    try:
                        client.write_message(msg)
                    except Exception:
                        pass
        except Exception as e:
            print(f"Sync error: {e}. Retrying in 5s...")
            await asyncio.sleep(5)


# --- 4. ЗАПУСК ---
if __name__ == "__main__":

    def dummy_cb(*args, **kwargs):
        pass

    host = SlaveHost(
        hmi=FakeHMI(init_cb=dummy_cb), prefs=MockPrefs(), msg_callback=dummy_cb
    )

    import mod.webserver as ws_mod

    def wrap(name):
        cls = getattr(ws_mod, name, None)
        if not cls:
            return None
        for m in ["get", "post"]:
            if hasattr(cls, m):
                setattr(cls, m, make_native_coroutine(getattr(cls, m)))
        return cls

    LV2_PATH = "/var/modep/lv2"

    handlers = [
        (r"/websocket/?$", wrap("ServerWebSocket")),
        (r"/ping/?", wrap("Ping")),
        (r"/effect/list", wrap("EffectList")),
        (r"/effect/get", wrap("EffectGet")),
        (r"/pedalboard/info/?", wrap("PedalboardInfo")),
        (r"/pedalboard/load_web/?", wrap("PedalboardLoadWeb")),
        (r"/js/templates.js$", wrap("BulkTemplateLoader")),
        (r"/load_template/([a-z_]+\.html)$", wrap("TemplateLoader")),
        (r"/resources/(.*)", tornado.web.StaticFileHandler, {"path": LV2_PATH}),
        (r"/(index.html)?$", wrap("TemplateHandler")),
        (r"/([a-z]+\.html)$", wrap("TemplateHandler")),
        (r"/(allguis|settings)$", wrap("TemplateHandler")),
        (
            r"/(.*)",
            tornado.web.StaticFileHandler,
            {"path": HTML_DIR, "default_filename": "index.html"},
        ),
    ]

    handlers = [h for h in handlers if h[1] is not None]
    app = tornado.web.Application(handlers, host=host, debug=True)
    webserver.GlobalWebServerState.ws_clients = []

    app.listen(8080)
    print("[+] Slave UI active on http://localhost:8080")

    loop = tornado.ioloop.IOLoop.current()
    loop.spawn_callback(sync_with_master, app)
    loop.start()
