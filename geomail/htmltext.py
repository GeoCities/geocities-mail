import html2text


def html_to_text(html: str) -> str:
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.body_width = 0
    h.ignore_emphasis = False
    return h.handle(html or "").strip()


def body_text(msg) -> str:
    if msg.text and msg.text.strip():
        return msg.text
    if msg.html:
        return html_to_text(msg.html)
    return msg.preview or ""


def quote(text: str) -> str:
    return "\n".join("> " + line for line in text.splitlines())
