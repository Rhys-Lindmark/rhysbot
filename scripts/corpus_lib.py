"""Small, dependency-free corpus primitives. Raw inputs must remain outside Git."""
import hashlib
import html
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'li', 'blockquote'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if tag in ('p', 'div', 'li', 'blockquote'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_text(text, markup=False):
    if markup:
        parser = TextExtractor()
        parser.feed(text)
        text = ''.join(parser.parts)
    text = html.unescape(text).replace('\xa0', ' ')
    text = re.sub(r'\[sound:[^\]]+\]', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_jsonl(path):
    with Path(path).open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path, rows):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('w') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


# Conservative exclusion, not a substitute for semantic review of local Anki.
RULES = {
    'contact_email': r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',
    'phone': r'(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)',
    'ssn': r'\b\d{3}-\d{2}-\d{4}\b',
    'credential': r'(?i)(?:\b(?:password|passphrase|api[ _-]?key|access[ _-]?token|private[ _-]?key|seed phrase)\b|\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{15,})\b|BEGIN .*PRIVATE KEY)',
    'private_path': r'(?i)(?:/Users/|/home/|file://|[A-Z]:\\Users\\)',
    'signed_access_url': r'https?://\S*[?&](?:token|access_token|auth|api_key|signature|x-amz-signature|x-goog-signature|x-amz-credential|key-pair-id)=',
    'relationship_or_intimacy': r'\b(?:dating|girlfriend|boyfriend|fianc[eé]|tinder|hinge|bumble|okcupid|polyamory|polyamorous|sexual|sex life|hookup|hooking up|crush|breakup|break-up|divorce|intimacy|libido|orgasm|porn|nudes?)\b',
    'family': r'\b(?:family|families|mom|mum|dad|mother|father|parents?|siblings?|brothers?|sisters?|wife|wives|husband|spouse|grandma|grandpa|grandmother|grandfather|cousins?|aunt|uncle|nephew|niece|daughter|son|children|kids)\b',
    'personal_health_or_finance': r'\b(?:my diagnosis|my therapist|my therapy|my medication|my salary|my bank|net worth|stolen crypto|routing number|account number|credit card|social security|passport number|suicidal|self.harm)\b',
    'street_address': r'\b\d{1,5}\s+(?:[A-Za-z]+\s+){0,3}(?:Street|Avenue|Boulevard|Road|Lane|Drive|Court|St\.|Ave\.|Blvd\.|Rd\.)\b',
    'private_marker': r'\b(?:confidential|do not share|not for publication|private journal|private notes|direct message|DMs)\b',
}
COMPILED = {name: re.compile(pattern, re.I) for name, pattern in RULES.items()}


def privacy_flags(text, local=False):
    flags = [name for name, rx in COMPILED.items() if rx.search(text)]
    if local and re.search(r'\b(?:my|mine|me|myself|our|ourselves|we|I|Rhys|RL)\b', text, re.I):
        flags.append('personal_reference')
    # Large identifiers are not useful educational prose; keep them private.
    if re.search(r'(?<!\d)\d{13,19}(?!\d)', re.sub(r'https?://\S+', '', text)):
        flags.append('long_identifier')
    return sorted(set(flags))


CONTEXTUAL_FLAGS = {'personal_reference', 'family', 'relationship_or_intimacy',
                    'personal_health_or_finance'}
CONTEXTUAL_RATIONALE = ('Read complete question, answer, and citation; no private personal-life details. '
                        'General source material retained even when incomplete or image-dependent.')


def canonical_anki_text(front, back, citation):
    text = 'Question: ' + front + '\nAnswer: ' + back
    return text + ('\nSource: ' + citation if citation else '')


@lru_cache(maxsize=1)
def pattern_false_positives():
    """Ten individually re-read, exact-content exceptions, never a topic waiver."""
    path = Path(__file__).resolve().parents[1]/'data/anki-pattern-false-positives.json'
    if not path.exists():
        return {}
    entries = json.loads(path.read_text())
    if len({e['id'] for e in entries}) != len(entries):
        raise ValueError('Duplicate pattern adjudication')
    return {e['id']: e for e in entries}


def anki_release_approved(record, approval):
    """Hash-bound reading may resolve topic flags; contacts/secrets stay blocked.

    This validates a persisted semantic judgment, never creates one. An image,
    empty answer or generic first-person wording alone is not personal data.
    """
    if not (approval and approval.get('decision') == 'approve'
            and approval.get('content_sha256') == digest(record['text'])):
        return False
    fields = [record.get(k) for k in ('front', 'back', 'citation')]
    if not all(isinstance(value, str) for value in fields) or canonical_anki_text(*fields) != record['text']:
        return False
    flags = privacy_flags(record['text'], local=True)
    contextual = approval.get('contextual_privacy_review') is True
    if not contextual:
        return not flags and not record.get('privacy_flags')
    reviewed = approval.get('approved_privacy_flags')
    extra = set(flags) - CONTEXTUAL_FLAGS
    if extra:
        exception = pattern_false_positives().get(record['id'], {})
        if (not extra <= {'credential', 'private_marker', 'street_address'}
                or exception.get('content_sha256') != digest(record['text'])
                or exception.get('flags') != flags
                or exception.get('reviewer') != 'codex-manager-second-semantic-review'):
            return False
    return bool(isinstance(reviewed, list) and reviewed == flags
                and approval.get('supersedes_cached_flags') is True
                and approval.get('reviewer') == 'codex-semantic-review'
                and approval.get('scope') == 'privacy_only_not_factual_validation'
                and approval.get('rationale') == CONTEXTUAL_RATIONALE)


def group_split(group_id):
    bucket = int(digest(group_id)[:8], 16) % 100
    return 'test' if bucket < 5 else 'validation' if bucket < 10 else 'train'
