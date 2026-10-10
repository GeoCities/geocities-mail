"""Headless TUI smoke test with the demo backend (Textual pilot)."""
from textual.widgets import DataTable, Static
from geomail.tui import ComposeScreen, GeoMailApp


async def test_tui_smoke(demo):
    app = GeoMailApp(demo)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#messages", DataTable)
        assert table.row_count == 3
        assert app.current is not None and app.current.id == "d1"
        assert not demo.get_message("d1").unread  # opening marks read
        await pilot.press("down"); await pilot.pause()
        assert app.current.id == "d2"
        assert "3D dancing baby" in str(app.query_one("#body", Static).render())
        await pilot.press("u"); await pilot.pause()
        assert demo.get_message("d2").unread
        await pilot.press("r"); await pilot.pause()
        assert isinstance(app.screen, ComposeScreen)
        await pilot.press("escape"); await pilot.pause()
        await pilot.press("c"); await pilot.pause()
        scr = app.screen
        scr.query_one("#to").value = "pal@example.invalid"
        scr.query_one("#subject").value = "Guestbook"
        await pilot.press("ctrl+s"); await pilot.pause()
        assert any(m.subject == "Guestbook" for m in demo.list_messages("Sent"))
        await pilot.press("slash"); await pilot.pause()
        await pilot.press(*"counter", "enter"); await pilot.pause()
        assert table.row_count == 1
        await pilot.press("d"); await pilot.pause()
        assert demo.get_message("d3").folder == "Trash"
        await pilot.press("q")
