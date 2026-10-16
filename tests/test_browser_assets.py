"""Exercise the shipped page and assets rather than placeholder test HTML."""

from html.parser import HTMLParser
from http.client import HTTPConnection
import json
import threading

from gamescoach.storage import Store
from gamescoach.web import create_server


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.token = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "script" and attributes.get("src"):
            self.assets.append((attributes["src"], "text/javascript"))
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.assets.append((attributes["href"], "text/css"))
        if tag == "meta" and attributes.get("name") == "gamescoach-token":
            self.token = attributes.get("content")


def test_shipped_browser_page_can_load_assets_and_initialize_without_api_calls(tmp_path):
    server = create_server(Store(tmp_path / "data"), demo=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    connection = HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        page = PageAssets()
        page.feed(response.read().decode("utf-8"))
        assert page.token and page.token != "{{CSRF_TOKEN}}"
        assert page.assets
        for path, mime in page.assets:
            assert path.startswith("/") and not path.startswith("//")
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 200, path
            assert response.getheader("Content-Type").split(";", 1)[0] == mime
            assert response.read().strip()
        connection.request("GET", "/api/state", headers={"X-GamesCoach-Token": page.token})
        response = connection.getresponse()
        assert response.status == 200
        state = json.loads(response.read())
        assert state["demo"] is True and state["sessions"] == []
        assert not (tmp_path / "data" / "sessions").exists()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
