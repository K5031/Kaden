from app.registry import command


@command("ping")
def ping():
    """Whether the app answers."""
    return {"ok": True}


@command("echo")
def echo(text=""):
    """`text`, back."""
    return {"ok": True, "text": text}
