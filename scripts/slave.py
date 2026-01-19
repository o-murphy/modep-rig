import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.httpclient
from tornado.websocket import websocket_connect

MASTER_URL = "http://192.168.88.247"  # IP вашого Master пристрою
MASTER_WS = "ws://192.168.88.247/websocket"

# Налаштовуємо клієнт для великої кількості запитів (картинки плагінів)
http_client = tornado.httpclient.AsyncHTTPClient(max_clients=100)


class WSProxyHandler(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin):
        return True

    async def open(self):
        try:
            self.master_link = await websocket_connect(MASTER_WS)
            tornado.ioloop.IOLoop.current().spawn_callback(self.relay_from_master)
            print("WS: Connection established with Master")
        except Exception as e:
            print(f"WS: Failed to connect to Master: {e}")
            self.close()

    async def relay_from_master(self):
        while True:
            try:
                msg = await self.master_link.read_message()
                if msg is None:
                    break
                self.write_message(msg)
            except Exception:
                break

    def on_message(self, message):
        if hasattr(self, "master_link") and self.master_link:
            self.master_link.write_message(message)

    def on_close(self):
        if hasattr(self, "master_link"):
            self.master_link.close()


class HttpProxyHandler(tornado.web.RequestHandler):
    async def get(self, path):
        # Отримуємо query string (параметри після ?)
        uri = f"{MASTER_URL}/{path}"
        if self.request.query:
            uri += f"?{self.request.query}"

        try:
            resp = await http_client.fetch(uri, request_timeout=10.0)
            self.set_status(resp.code)

            # Копіюємо заголовки (важливо для Content-Type картинок)
            for header, value in resp.headers.items():
                if header not in [
                    "Content-Length",
                    "Transfer-Encoding",
                    "Content-Encoding",
                ]:
                    self.set_header(header, value)

            self.write(resp.body)
        except tornado.httpclient.HTTPClientError as e:
            self.set_status(e.code)
        except Exception as e:
            print(f"Error proxying {path}: {e}")
            self.set_status(500)
        self.finish()


def make_app():
    return tornado.web.Application(
        [
            # Обробляємо і /ws, і /websocket (деякі версії mod-ui використовують обидва)
            (r"/ws", WSProxyHandler),
            (r"/websocket", WSProxyHandler),
            # Решта запитів
            (r"/(.*)", HttpProxyHandler),
        ],
        debug=False,
    )


if __name__ == "__main__":
    app = make_app()
    app.listen(8080)
    print(f"Slave Mirror started on :8080. Mirroring {MASTER_URL}")
    tornado.ioloop.IOLoop.current().start()
