COMMANDS = {}


def command(name):
    """Register the decorated function as the command `name`."""
    def register(function):
        COMMANDS[name] = function
        return function
    return register


def run(name, **arguments):
    """The result of command `name`: always a dict."""
    return COMMANDS[name](**arguments)
