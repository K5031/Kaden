DEFAULT_PORT = 8080


def parse_port(text):
    """The port in `text`, or the default when it is empty."""
    text = text.strip()
    if not text:
        return DEFAULT_PORT
    port = int(text)
    if not 0 < port < 65536:
        raise ValueError(f"no such port: {port}")
    return port
