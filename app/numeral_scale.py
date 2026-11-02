"""Check explicit Indian counting units without guessing currencies or speech."""
import re
from decimal import Decimal, localcontext


_INDIAN = {'crore': 10_000_000, 'crores': 10_000_000, 'கோடி': 10_000_000,
           'करोड़': 10_000_000, 'lakh': 100_000, 'lakhs': 100_000, 'लाख': 100_000}
_SWEDISH = {'miljard': 1_000_000_000, 'miljarder': 1_000_000_000,
            'miljarden': 1_000_000_000, 'miljarderna': 1_000_000_000,
            'miljon': 1_000_000, 'miljoner': 1_000_000,
            'miljonen': 1_000_000, 'miljonerna': 1_000_000, 'tusen': 1000}
_WORDS = dict(zip(('en', 'ett', 'två', 'tre', 'fyra', 'fem', 'sex', 'sju', 'åtta', 'nio', 'tio',
                  'elva', 'tolv', 'tjugo', 'femtio', 'hundra'),
                 (1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 20, 50, 100)))
_DIGITS = r'\d+(?:[.,]\d+)?'
_NUMBER = re.compile(r'(?<![\w.,])' + _DIGITS + r'(?![\d.,])')
_SOURCE = re.compile(r'(?<![\w.,])(?P<number>' + _DIGITS + r')\s*(?P<unit>' +
                     '|'.join(map(re.escape, _INDIAN)) + r')(?!\w)', re.I)
_UNITS = '|'.join(map(re.escape, sorted({**_INDIAN, **_SWEDISH}, key=len, reverse=True)))
_TARGET = re.compile(r'(?<![\w.,])(?:(?P<digits>' + _DIGITS + r')\s*(?P<unit>' + _UNITS +
                     r')?|(?P<word>' + '|'.join(_WORDS) + r')\s+(?P<word_unit>' + _UNITS + r'))(?!\w|[.,]\d)', re.I)


def _number(text):
    # "1,000" can mean a decimal or a thousands group. Do not guess which.
    if re.search(r'[.,]\d{3}$', text):
        return None
    return Decimal(text.replace(',', '.'))


def numeral_scale_flags(rows, target_key='sv'):
    """One unambiguous digit amount per row; complex/grouped amounts stay with review.

    These units express counts, not exchange rates. Never rewrite text here.
    The existing translator/repairer and independent final review do the repair.
    """
    if target_key != 'sv':
        return []
    flags = []
    for row in rows:
        source, target = str(row.get('source', '')), str(row.get(target_key) or '')
        amounts = list(_SOURCE.finditer(source))
        translated = list(_TARGET.finditer(target))
        if len(amounts) != 1 or len(_NUMBER.findall(source)) != 1 or len(translated) != 1:
            continue
        amount, other = amounts[0], translated[0]
        if (not other['unit'] and not other['word_unit']
                and re.match(r'\s*(?:miljon|miljard|crore|lakh)', target[other.end():], re.I)):
            continue  # An unparsed inflection/compound is not a plain digit count.
        value = _number(amount['number'])
        actual = _number(other['digits']) if other['digits'] else Decimal(_WORDS[other['word'].casefold()])
        if value is None or actual is None:
            continue
        expected = value * _INDIAN[amount['unit'].casefold()]
        actual *= {**_INDIAN, **_SWEDISH}.get((other['unit'] or other['word_unit'] or '').casefold(), 1)
        if expected != actual:
            flags.append({'id': row['id'], 'category': 'meaning', 'source_quote': amount.group(),
                'target_quote': other.group(), 'reason':
                f'Talets storlek är fel: {amount.group()} motsvarar {expected:f}, inte {actual:f}. '
                'En crore är 10 miljoner och en lakh 100 000. Bevara mängden och valutan; gör ingen valutaomräkning.'})
    return flags


def repair_standalone_scales(rows, flags, target_key='sv'):
    """Repair only a flagged, standalone amount by changing its digit coefficient.

    No currency, sentence, negation, spelled number, singular/definite unit or
    ambiguous amount is rewritten. The independent final review still applies.
    """
    result, changes = [dict(row) for row in rows], []
    if target_key != 'sv':
        return result, changes
    requested = {(f.get('id'), f.get('source_quote'), f.get('target_quote'))
                 for f in flags if f.get('category') == 'meaning'}
    for row in result:
        for finding in numeral_scale_flags([row]):
            key = (row['id'], finding['source_quote'], finding['target_quote'])
            if key not in requested:
                continue
            source, target = row['source'], row['sv']
            amount, other = _SOURCE.search(source), _TARGET.search(target)
            if (not other['digits'] or (other['unit'] or '').casefold() not in ('miljoner', 'miljarder', 'tusen')
                    or source[:amount.start()].strip() or target[:other.start()].strip()
                    or not re.fullmatch(r'[\s.!?…]*', source[amount.end():])
                    or not re.fullmatch(r'[\s.!?…]*', target[other.end():])):
                continue
            with localcontext() as context:
                context.prec = max(50, len(amount['number']) + 20)
                expected = _number(amount['number']) * _INDIAN[amount['unit'].casefold()]
                coefficient = expected / _SWEDISH[other['unit'].casefold()]
            if coefficient == 1:
                continue  # Would also require a singular unit; leave it to the model.
            digits = format(coefficient, 'f')
            if '.' in digits:
                digits = digits.rstrip('0').rstrip('.')
            digits = digits.replace('.', ',')
            before = row['sv']
            proposed = target[:other.start('digits')] + digits + target[other.end('digits'):]
            if numeral_scale_flags([{**row, 'sv': proposed}]):
                continue
            row['sv'] = proposed
            changes.append({**finding, 'before': before, 'after': proposed})
    return result, changes
