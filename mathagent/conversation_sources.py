"""Resolve source references from the human's current request and attachments."""
import re
import unicodedata

from .worker_errors import WorkerInputError


_NAMED = re.compile(r'(?<![\w./-])([\w-]+(?:/[\w.-]+)*\.(?:pdf|tex|md|txt))(?![\w-]|\.\w)', re.I)


def conversation_sources(query, supplied, transcript):
    """Inherit only an unambiguous user attachment for a source-dependent follow-up.

    Worker output, workspace discovery and attached document contents cannot
    select another source. New explicit attachments always take precedence.
    """
    if supplied:
        return list(supplied)
    named = list(dict.fromkeys(_NAMED.findall(query)))
    if named:
        return named
    text = ''.join(c for c in unicodedata.normalize('NFKD', query).casefold()
                   if not unicodedata.combining(c))
    target = bool(re.search(r'\b(?:theorem|lemma|proposition|corollary|theoreme|lemme)\s+(?:\d+(?:\.\d+)*|[a-z])\b', text))
    pointer = bool(re.search(r'\b(?:this|that|attached|same|previous|the|cet|cette|ce|le|la|meme|precedent)\s+'
                             r'(?:article|paper|manuscript|document|proof|result|preuve|resultat)\b', text))
    plural = bool(re.search(r'\b(?:these |those |all (?:the )?|ces |tous les |toutes les )(?:articles|papers|manuscripts|documents)\b', text))
    if not (target or pointer or plural):
        return []
    previous = next((item.get('files', []) for item in reversed(transcript)
                     if item.get('role') == 'user' and item.get('files')), [])
    previous = list(dict.fromkeys(previous))
    if len(previous) <= 1:
        return previous
    if plural:
        return previous
    raise WorkerInputError('Which source should I use: ' + ', '.join(previous) + '?',
                           code='ambiguous_source', details={'sources': previous})
