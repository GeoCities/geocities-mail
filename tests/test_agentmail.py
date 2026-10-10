"""AgentMail backend against httpx.MockTransport (no network)."""
import json
import httpx
from geomail.backends.agentmail import AgentMailBackend
from geomail.models import Outgoing

MSG = {"inbox_id": "bot@agentmail.to", "thread_id": "t1", "message_id": "<abc@agentmail.to>", "labels": ["received", "unread"],
       "timestamp": "2026-10-10T12:00:00Z", "from": "Alice <a@example.com>", "to": ["bot@agentmail.to"],
       "subject": "Hi", "preview": "yo", "html": "<p>Hello <b>bot</b></p>",
       "attachments": [{"attachment_id": "att1", "filename": "f.txt", "size": 3, "content_type": "text/plain"}]}


def make():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        p = req.url.path
        assert req.headers["authorization"] == "Bearer KEY"
        if p == "/v0/inboxes":
            return httpx.Response(200, json={"count": 1, "inboxes": [{"inbox_id": "bot@agentmail.to"}]})
        if p.endswith("/messages") and req.method == "GET":
            return httpx.Response(200, json={"count": 1, "messages": [MSG]})
        if p.endswith("/messages/search"):
            return httpx.Response(200, json={"count": 1, "messages": [MSG]})
        if p.endswith("/attachments/att1"):
            return httpx.Response(200, json={"attachment_id": "att1", "download_url": "https://files.example/att1"})
        if p.endswith("/messages/send") or p.endswith("/reply"):
            return httpx.Response(200, json={"message_id": "<new@x>", "thread_id": "t1"})
        if "/messages/" in p and req.method in ("GET", "PATCH"):
            return httpx.Response(200, json=MSG)
        if req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"error": p})

    client = httpx.Client(base_url="https://api.agentmail.to", transport=httpx.MockTransport(handler),
                          headers={"Authorization": "Bearer KEY"})
    return AgentMailBackend(api_key="KEY", client=client), calls


def test_list_read_flags():
    b, calls = make()
    assert b.inbox_id == "bot@agentmail.to"
    msgs = b.list_messages("INBOX")
    assert msgs[0].unread and msgs[0].attachments[0].filename == "f.txt"
    assert calls[-1].url.params["labels"] == "received"
    m = b.get_message(msgs[0].id)
    assert "%3Cabc%40agentmail.to%3E" in str(calls[-1].url)
    b.set_read(m.id, True)
    assert json.loads(calls[-1].content) == {"remove_labels": ["unread"], "add_labels": ["read"]}
    b.list_messages(query="hello")
    assert calls[-1].url.path.endswith("/messages/search") and calls[-1].url.params["q"] == "hello"
    b.delete(m.id)
    assert calls[-1].method == "DELETE"


def test_send_and_reply(tmp_path):
    b, calls = make()
    f = tmp_path / "x.txt"; f.write_text("abc")
    assert b.send(Outgoing(to=["z@example.invalid"], subject="S", body="B", attachments=[str(f)])) == "<new@x>"
    body = json.loads(calls[-1].content)
    assert calls[-1].url.raw_path == b"/v0/inboxes/bot%40agentmail.to/messages/send"
    assert body["subject"] == "S" and body["attachments"][0]["content"] == "YWJj"
    b.send(Outgoing(to=[], subject="", body="ok", in_reply_to="<abc@agentmail.to>", reply_all=True))
    body = json.loads(calls[-1].content)
    assert calls[-1].url.path.endswith("/reply") and body["reply_all"] is True and "to" not in body
