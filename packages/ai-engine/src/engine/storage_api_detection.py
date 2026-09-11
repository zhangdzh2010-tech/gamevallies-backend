"""Inspect storage capabilities in executable HTML contexts, not prose/comments.

This remains conservative about string literals: a string can supply a computed
property name. It is a contract check, not a JavaScript security sandbox.
"""
from html.parser import HTMLParser
import re


class _ExecutableContexts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments = []
        self.embedded_documents = []
        self.in_script = False
        self.saw_tag = False

    def handle_starttag(self, tag, attrs):
        self.saw_tag = True
        attributes = dict(attrs)
        if tag == 'script':
            script_type = (attributes.get('type') or '').strip().lower()
            # Known data blocks are inert. Keep unfamiliar script types in the
            # analysis rather than silently assuming they cannot execute.
            self.in_script = script_type not in {
                'application/json', 'application/ld+json', 'importmap', 'speculationrules',
            }
        for name, value in attrs:
            if not value:
                continue
            if name.startswith('on'):
                self.fragments.append(value)
            elif name in {'href', 'src', 'action', 'formaction', 'xlink:href'}:
                url = re.sub(r'[\x00-\x20]', '', value)
                if url.lower().startswith('javascript:'):
                    self.fragments.append(url[len('javascript:'):])
            elif name == 'srcdoc':
                self.embedded_documents.append(value)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.fragments.append(data)


def contains_storage_api_usage(code: str, api_name: str, *, _depth=0) -> bool:
    pattern = re.compile(r'(?<![\w$])' + re.escape(api_name) + r'(?![\w$])', re.I)
    try:
        parser = _ExecutableContexts()
        parser.feed(code)
        parser.close()
        fragments = parser.fragments if parser.saw_tag else [code]
        for document in parser.embedded_documents:
            if (_depth >= 4 and pattern.search(document)) or (
                _depth < 4 and contains_storage_api_usage(document, api_name, _depth=_depth + 1)
            ):
                return True
        import esprima
        for script in fragments:
            try:
                tokens = esprima.tokenize(script, {'comment': True})
            except Exception:
                # Unknown lexical boundaries retain the old conservative check.
                if pattern.search(script):
                    return True
                continue
            for token in tokens:
                if token.type not in {'LineComment', 'BlockComment', 'RegularExpression'} and pattern.search(token.value):
                    return True
        return False
    except Exception:
        return bool(pattern.search(code))
