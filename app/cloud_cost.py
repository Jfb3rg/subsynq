"""Local, user-editable cloud GPU estimates; no provider billing or rentals."""
import json
import math
from html import escape

from cloud_market import VRAM_BAND_LABELS, VRAM_BANDS, normalize_vram_bands


DEFAULT_PROFILE = {
    'provider': 'vast', 'gpu': 'RTX 5060 Ti', 'mode': 'test',
    'compute_hourly': None, 'storage_hourly': None,
    'transfer_per_gb': None, 'max_minutes': 60, 'currency': 'USD',
}


def _price(value):
    if value is None or str(value).strip() == '':
        return None
    price = float(value)
    if not math.isfinite(price) or not 0 <= price <= 10000:
        raise ValueError('Pris måste vara mellan 0 och 10 000.')
    return round(price, 4)


def parse_profile(form, *, allow_legacy_currency=False):
    def field(name, default=''):
        return form.get(name, [default])[0]
    provider = field('provider')
    mode = field('mode')
    currency = field('currency')
    gpu = ' '.join(field('gpu').split())
    if provider not in ('vast', 'runpod') or mode not in ('test', 'permanent'):
        raise ValueError('Ogiltig molnprofil.')
    valid_currencies = ('USD', 'SEK', 'EUR') if allow_legacy_currency else ('USD',)
    if currency not in valid_currencies or not 1 <= len(gpu) <= 80:
        raise ValueError('Ogiltig valuta eller GPU-modell.')
    try:
        minutes = int(field('max_minutes'))
    except ValueError as error:
        raise ValueError('Ange ett helt antal minuter.') from error
    if not 1 <= minutes <= 10080:
        raise ValueError('Maximal tid måste vara 1–10 080 minuter.')
    return {
        'provider': provider, 'gpu': gpu, 'mode': mode,
        'compute_hourly': _price(field('compute_hourly')),
        'storage_hourly': _price(field('storage_hourly')),
        'transfer_per_gb': _price(field('transfer_per_gb')),
        'max_minutes': minutes, 'currency': currency,
    }


def load_profile(raw):
    try:
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            raise ValueError()
        return parse_profile({key: [payload.get(key, value)] for key, value in
                              DEFAULT_PROFILE.items()}, allow_legacy_currency=True)
    except (ValueError, TypeError, KeyError):
        return dict(DEFAULT_PROFILE)


def render_cloud_cost(profile, token, saved=False):
    profile = load_profile(json.dumps(profile))
    legacy_currency = profile['currency'] != 'USD'
    def selected(field, value):
        return ' selected' if profile[field] == value else ''
    def value(field):
        if legacy_currency and field in ('compute_hourly', 'storage_hourly', 'transfer_per_gb'):
            return ''
        entry = profile[field]
        return '' if entry is None else escape(str(entry), quote=True)
    compute = profile['compute_hourly']
    storage = profile['storage_hourly']
    hours = profile['max_minutes'] / 60
    if legacy_currency:
        estimate = (f'Den äldre planen är i {profile["currency"]}. Beloppen används inte som USD. '
                    'Välj ett aktuellt GPU-pris ovan eller ange nya USD-belopp nedan.')
    elif compute is None:
        estimate = 'Ange ett GPU-pris för att se en uppskattning.'
    else:
        amount = compute * hours
        if storage is not None:
            amount += storage * hours
        estimate = (f'För {profile["max_minutes"]} minuter: cirka {amount:.2f} '
                    f'{profile["currency"]}' +
                    (' inklusive angiven lagring.' if storage is not None else
                     ' plus lagring och eventuell dataöverföring.'))
    saved_notice = '<p class="success" role="status">✓ Kostnadsantaganden sparade.</p>' if saved else ''
    return (
        '<div class="card"><h2>Egen kostnadsplan</h2>'
        '<p class="muted">Du kan ändra alla belopp manuellt för både test och längre användning. '
        'Det här är din egen kalkyl, inte leverantörens faktura. Ingen moln-GPU startas här.</p>'
        f'{saved_notice}<form action="/cloud-cost" method="post" class="cloud-cost-form">'
        f'<input type="hidden" name="token" value="{escape(token, quote=True)}">'
        f'<input type="hidden" name="provider" value="{value("provider")}">'
        f'<input type="hidden" name="gpu" value="{value("gpu")}">'
        f'<p>Planens modell: <strong>{escape(profile["provider"].title())} · {escape(profile["gpu"])}</strong>. '
        'Välj ny GPU i listan ovan utan konto. Det här är ett kostnadsantagande, '
        'inte en garanterat tillgänglig GPU.</p>'
        '<label for="cloud-mode">Användning</label>'
        f'<select id="cloud-mode" name="mode"><option value="test"{selected("mode", "test")}>Test</option>'
        f'<option value="permanent"{selected("mode", "permanent")}>Längre användning</option></select>'
        '<p class="muted">Alla belopp på den här sidan anges i USD. Ingen automatisk valutaväxling görs.</p>'
        '<label for="cloud-compute">GPU-pris per timme (USD)</label>'
        f'<input id="cloud-compute" name="compute_hourly" type="number" min="0" max="10000" step="0.0001" value="{value("compute_hourly")}">'
        '<label for="cloud-storage">Lagring per timme (USD)</label>'
        f'<input id="cloud-storage" name="storage_hourly" type="number" min="0" max="10000" step="0.0001" value="{value("storage_hourly")}">'
        '<label for="cloud-transfer">Dataöverföring per GB (USD)</label>'
        f'<input id="cloud-transfer" name="transfer_per_gb" type="number" min="0" max="10000" step="0.0001" value="{value("transfer_per_gb")}">'
        '<label for="cloud-minutes">Planerad eller maximal tid i minuter</label>'
        f'<input id="cloud-minutes" name="max_minutes" type="number" min="1" max="10080" value="{value("max_minutes")}">'
        '<input type="hidden" name="currency" value="USD">'
        '<button type="submit">Spara kostnadsplan</button></form>'
        f'<p><strong>{escape(estimate)}</strong></p>'
        '<p class="muted">Överföring läggs till först när mängden data är känd. '
        'Lagring kan fortsätta kosta efter att en instans stoppats. '
        'Att spara här hyr eller startar inget.</p></div>'
    )


def render_cloud_market(provider, connected, result, token, notice='', gpu_query='', vram_bands=()):
    """Render only offers returned by the selected provider's current API response."""
    vram_bands = normalize_vram_bands(vram_bands)
    vram_hidden = ''.join(f'<input type="hidden" name="vram" value="{band}">' for band in vram_bands)
    if provider not in ('vast', 'runpod'):
        provider = 'vast'
    label = 'Vast.ai' if provider == 'vast' else 'Runpod'
    key_url = ('https://console.vast.ai/manage-keys/' if provider == 'vast'
               else 'https://www.console.runpod.io/user/settings')
    notice_html = f'<p class="success" role="status">{escape(notice)}</p>' if notice else ''
    vast_current = ' aria-current="page"' if provider == 'vast' else ''
    runpod_current = ' aria-current="page"' if provider == 'runpod' else ''
    links = (
        '<p class="settings-subnav">'
        f'<a href="/settings/cloud?provider=vast"{vast_current}>Vast.ai</a>'
        ' · '
        f'<a href="/settings/cloud?provider=runpod"{runpod_current}>Runpod</a></p>'
    )
    key_form = (
        f'<form action="/cloud-key" method="post"><input type="hidden" name="token" value="{escape(token, quote=True)}">'
        f'<input type="hidden" name="provider" value="{provider}">'
        '<label for="api-key">API-nyckel</label>'
        '<input id="api-key" name="api_key" type="password" autocomplete="off" required minlength="8" maxlength="512" '
        'placeholder="Klistra in nyckeln här — visas inte igen">'
        f'<button type="submit">{("Byt" if connected else "Spara")} API-nyckel</button></form>'
    )
    remove_form = (
        f'<form action="/cloud-key/remove" method="post" onsubmit="return confirm(\'Ta bort API-nyckeln för {label}?\')">'
        f'<input type="hidden" name="token" value="{escape(token, quote=True)}">'
        f'<input type="hidden" name="provider" value="{provider}">'
        '<button class="small-button danger" type="submit">Ta bort nyckel</button></form>'
    ) if connected else ''
    offers = result.get('offers') or []
    vram_boxes = ''.join(
        f'<label class="vram-choice"><input type="checkbox" name="vram" value="{key}"'
        f'{" checked" if key in vram_bands else ""} onchange="this.form.submit()"> '
        f'{VRAM_BAND_LABELS[key]}</label>' for key, _low, _high in VRAM_BANDS)
    name_search = (
        '<label for="gpu-query">Sök exakt GPU-modell hos Vast.ai</label>'
        f'<input id="gpu-query" name="gpu" value="{escape(gpu_query, quote=True)}" '
        'placeholder="t.ex. RTX 5060 Ti" maxlength="80">'
    ) if provider == 'vast' else ''
    search = (
        '<form action="/settings/cloud" method="get" class="vram-filter">'
        f'<input type="hidden" name="provider" value="{provider}">'
        + name_search +
        '<fieldset><legend>Visa bara GPU med så här mycket minne</legend>' + vram_boxes + '</fieldset>'
        f'<button type="submit">{"Sök GPU" if provider == "vast" else "Visa"}</button></form>'
        '<p class="muted">Ingen ruta ikryssad visar alla storlekar. 16 GB omfattar även 20 GB-kort, '
        '24 GB är 23–30 GB, 32 GB är 31–32 GB och över 32 GB är 40 GB och uppåt. '
        + ('Tom sökning visar ett urval av de billigaste en-GPU-erbjudandena; '
           'sök på modell för att hitta även mindre vanliga GPU:er.' if provider == 'vast' else '')
        + '</p>'
    )
    chosen_text = ', '.join(VRAM_BAND_LABELS[key] for key in vram_bands)
    if result.get('error'):
        catalog_html = f'<p class="job-message">{escape(result["error"])}</p>'
    elif offers:
        options = ''.join(
            f'<option value="{escape(item["id"], quote=True)}">'
            f'{escape(item["gpu"])} · {item["price"]:.4f} USD/tim'
            f'{(" · " + escape(item["detail"])) if item.get("detail") else ""}</option>'
            for item in offers)
        catalog_html = (
            f'<p class="muted">Hämtat {escape(result.get("fetched_at") or "")}. '
            + (f'Visar bara {chosen_text} ({len(offers)} st). ' if vram_bands else '') +
            'Visade priser kan ändras utan förvarning; kontrollera hos leverantören innan köp.</p>'
            f'<form action="/cloud-offer" method="post"><input type="hidden" name="token" value="{escape(token, quote=True)}">'
            f'<input type="hidden" name="provider" value="{provider}">'
            f'<input type="hidden" name="gpu_query" value="{escape(gpu_query, quote=True)}">{vram_hidden}'
            f'<label for="live-offer">Aktuellt erbjudande från {label}</label>'
            f'<select id="live-offer" name="offer_id">{options}</select>'
            '<button type="submit">Använd priset i min kostnadsplan</button></form>'
            '<p class="muted">Det väljer bara ett prisunderlag. Det hyr, startar eller överför inget.</p>'
        )
    else:
        catalog_html = ('<p class="muted">Inga lediga en-GPU-erbjudanden med pris hittades just nu'
                        + (f' med minnesstorlek {chosen_text}. Prova en annan storlek eller ta bort filtret.</p>'
                           if vram_bands else '.</p>'))
    refresh = (
        f'<form action="/cloud-refresh" method="post"><input type="hidden" name="token" value="{escape(token, quote=True)}">'
        f'<input type="hidden" name="provider" value="{provider}">'
        f'<input type="hidden" name="gpu_query" value="{escape(gpu_query, quote=True)}">{vram_hidden}'
        '<button class="small-button" type="submit">Hämta aktuella erbjudanden</button></form>'
    )
    return (
        '<div class="card"><h2>Moln-GPU · aktuella erbjudanden</h2>' + links + notice_html
        +
        '<p class="muted">Aktuella GPU:er och priser hämtas direkt här utan konto. '
        'API-nyckel behövs inte för prislistan. En nyckel för framtida kontofunktioner '
        'sparas lokalt i /config och visas aldrig igen; använd minsta möjliga behörighet. '
        'Ingen instans hyrs eller startas från denna sida.</p>'
        + search + refresh + catalog_html
        + '<details><summary>Koppla konto för framtida molnkörning (valfritt för priser)</summary>'
        + f'<p><a href="{key_url}" target="_blank" rel="noopener noreferrer">Skapa/hitta API-nyckel hos {label} ↗</a></p>'
        + f'<p>API-nyckel för {label}: <strong>{"Sparad, men inte verifierad för molnkörning" if connected else "Ingen sparad"}</strong></p>'
        + key_form + remove_form + '</details></div>'
    )
