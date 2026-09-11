"""Recognize mouse registration syntax for an explicitly allowed desktop fallback.

This is static contract evidence. Runtime QA still checks that the handlers run
and change the game; registration alone cannot establish playable behavior.
"""
from .storage_api_detection import _ExecutableContexts

_EVENTS = {'click', 'mousedown', 'mousemove', 'mouseup'}


class _MouseContexts(_ExecutableContexts):
    inline_handler = False

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if any(name in {'on' + event for event in _EVENTS} and value and value.strip()
               for name, value in attrs):
            self.inline_handler = True


def has_registered_mouse_handler(code: str) -> bool:
    try:
        parser = _MouseContexts()
        parser.feed(code)
        parser.close()
        if parser.inline_handler:
            return True
        import esprima
        for fragment in parser.fragments if parser.saw_tag else [code]:
            # The lexer ignores comments and treats prose strings as single
            # tokens, so examples of registration cannot satisfy the contract.
            try:
                tokens = list(esprima.tokenize(fragment))
            except Exception:
                continue  # Uncertain syntax is not positive registration evidence.
            values = [token.value for token in tokens]
            for i, token in enumerate(tokens):
                if i == 0 or values[i - 1] != '.':
                    continue
                if token.type != 'Identifier':
                    continue
                if token.value in {'on' + event for event in _EVENTS}:
                    if i + 2 < len(tokens) and values[i + 1] == '=' and values[i + 2] not in {'null', 'undefined', 'false', '0'}:
                        return True
                if token.value != 'addEventListener' or i + 4 >= len(tokens):
                    continue
                if values[i + 1] != '(' or tokens[i + 2].type != 'String' or values[i + 3] != ',':
                    continue
                if values[i + 2] in {quote + event + quote for event in _EVENTS for quote in ("'", '"')}:
                    if values[i + 4] not in {'null', 'undefined', 'false', '0', ')'}:
                        return True
        return False
    except Exception:
        return False
