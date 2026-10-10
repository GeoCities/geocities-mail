import json
from geomail.backends.demo import DemoBackend
from geomail.cli import main
from geomail.models import Outgoing


def test_demo_basics(demo, tmp_path):
    inbox = demo.list_messages("INBOX")
    assert [m.id for m in inbox] == ["d1", "d2", "d3"]
    assert inbox[0].unread
    demo.set_read("d1", True)
    assert not demo.get_message("d1").unread
    assert demo.get_attachment("d1", "a1").startswith(b"GIF89a")
    demo.delete("d3")
    assert "d3" in [m.id for m in demo.list_messages("Trash")]
    f = tmp_path / "hi.txt"; f.write_text("hello")
    mid = demo.send(Outgoing(to=["x@example.invalid"], subject="t", body="b", attachments=[str(f)]))
    assert demo.get_attachment(mid, "x0") == b"hello"
    # persisted
    again = DemoBackend(state_path=demo.state_path)
    assert any(m.id == mid for m in again.list_messages("Sent"))


def test_demo_search(demo):
    assert [m.id for m in demo.list_messages(query="dancing")] == ["d2"]


def _cfg(tmp_path):
    c = tmp_path / "config.toml"
    c.write_text(f'default="demo"\n[accounts.demo]\nbackend="demo"\nstate_path="{tmp_path / "s.json"}"\n')
    return str(c)


def test_cli_json_roundtrip(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["--config", cfg, "--json", "list"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 3
    assert main(["--config", cfg, "--json", "read", "d2"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert "3D dancing baby" in d["body"] and "<b>" not in d["body"]
    assert main(["--config", cfg, "--json", "list", "--unread"]) == 0
    assert [m["id"] for m in json.loads(capsys.readouterr().out)] == ["d1"]
    assert main(["--config", cfg, "--json", "send", "--to", "a@example.invalid", "--subject", "S", "--body", "B"]) == 0
    assert json.loads(capsys.readouterr().out)["sent"]
    assert main(["--config", cfg, "--json", "list", "--folder", "Sent"]) == 0
    assert any(m["subject"] == "S" for m in json.loads(capsys.readouterr().out))
    assert main(["--config", cfg, "--json", "save-attachment", "d1", "a1", "-o", str(tmp_path)]) == 0
    assert (tmp_path / "under_construction.gif").exists()


def test_cli_error_json(tmp_path, capsys):
    assert main(["--config", _cfg(tmp_path), "--json", "read", "nope"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)
