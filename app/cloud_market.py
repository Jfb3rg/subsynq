"""Read-only marketplace catalog. No code path here can create a rental."""
import json
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PROVIDERS = {'vast': 'Vast.ai', 'runpod': 'Runpod'}
_cache = {}
_lock = threading.Lock()
_MAX_AGE = 300


def _credential_path(config):
    return Path(config) / 'cloud-api-keys.json'


def load_keys(config):
    try:
        data = json.loads(_credential_path(config).read_text(encoding='utf-8'))
        return {name: value for name, value in data.items()
                if name in PROVIDERS and isinstance(value, str) and value}
    except (OSError, ValueError, AttributeError):
        return {}


def _write_keys(path, keys):
    descriptor, temporary = tempfile.mkstemp(prefix='.cloud-api-keys-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(keys, output)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_key(config, provider, key):
    if provider not in PROVIDERS or not isinstance(key, str) or not 8 <= len(key) <= 512:
        raise ValueError('Ogiltig leverantör eller API-nyckel.')
    if any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ValueError('API-nyckeln får inte innehålla blanksteg eller kontrolltecken.')
    path = _credential_path(config)
    keys = load_keys(config)
    keys[provider] = key
    _write_keys(path, keys)
    with _lock:
        for entry in list(_cache):
            if entry[0] == provider:
                del _cache[entry]


def remove_key(config, provider):
    if provider not in PROVIDERS:
        raise ValueError('Ogiltig leverantör.')
    path = _credential_path(config)
    keys = load_keys(config)
    keys.pop(provider, None)
    if keys:
        _write_keys(path, keys)
    else:
        path.unlink(missing_ok=True)
    with _lock:
        for entry in list(_cache):
            if entry[0] == provider:
                del _cache[entry]


def normalize_gpu_query(value):
    name = ' '.join(str(value).replace('_', ' ').strip().split())
    if name and not re.fullmatch(r'[A-Za-z0-9 -]{2,80}', name):
        raise ValueError('Sök med ett GPU-namn, till exempel RTX 5060 Ti.')
    if name.lower().startswith('rtx '):
        parts = name.split(' ')
        name = ' '.join('Ti' if part.lower() == 'ti' else
                        'RTX' if part.lower() == 'rtx' else part for part in parts)
    return name


def _read_json(request):
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def _price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if math.isfinite(number) and 0 <= number <= 10000 else None


def normalize_vast(payload):
    offers = payload.get('offers') if isinstance(payload, dict) else None
    if not isinstance(offers, list):
        raise ValueError('Vast.ai skickade inget giltigt erbjudandesvar.')
    result = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        offer_id, gpu, price = offer.get('id'), offer.get('gpu_name'), _price(offer.get('dph_total'))
        if not isinstance(offer_id, int) or not isinstance(gpu, str) or not 1 <= len(gpu) <= 80 or price is None:
            continue
        if offer.get('rentable') is False or offer.get('rented') is True or offer.get('num_gpus', 1) != 1:
            continue
        vram = offer.get('gpu_ram')
        result.append({'id': str(offer_id), 'gpu': gpu, 'price': price,
                       'vram_gb': round(vram / 1024, 1) if isinstance(vram, (int, float)) and vram > 0 else None,
                       'detail': f"{offer.get('gpu_ram', 0) / 1024:.0f} GB VRAM" if isinstance(offer.get('gpu_ram'), (int, float)) else ''})
    return sorted(result, key=lambda item: (item['price'], item['gpu']))


# Lowest hardware a whole-chain CPU rental must have. Vast reports cpu_ram as 0
# for CPU-only offers, so memory cannot be filtered here; the rented service
# measures its own limit and refuses to start jobs if it is too small.
CPU_MIN_CORES = 16
CPU_MIN_DISK_GB = 80
CPU_MIN_DOWNLOAD_MBPS = 300
CPU_MIN_RELIABILITY = 0.95


def normalize_vast_cpu(payload):
    offers = payload.get('offers') if isinstance(payload, dict) else None
    if not isinstance(offers, list):
        raise ValueError('Vast.ai skickade inget giltigt erbjudandesvar.')
    result = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        offer_id, price = offer.get('id'), _price(offer.get('dph_total'))
        cores, disk = offer.get('cpu_cores_effective'), offer.get('disk_space')
        if not isinstance(offer_id, int) or price is None or offer.get('num_gpus') != 0:
            continue
        if offer.get('rentable') is False or offer.get('rented') is True:
            continue
        if not isinstance(cores, (int, float)) or cores < CPU_MIN_CORES:
            continue
        if not isinstance(disk, (int, float)) or disk < CPU_MIN_DISK_GB:
            continue
        if (offer.get('inet_down') or 0) < CPU_MIN_DOWNLOAD_MBPS:
            continue
        if (offer.get('reliability') or 0) < CPU_MIN_RELIABILITY:
            continue
        name = str(offer.get('cpu_name') or 'CPU').strip()[:60]
        result.append({'id': str(offer_id), 'gpu': 'CPU', 'price': price,
                       'detail': f'{name} · {cores:g} kärnor · {disk:.0f} GB disk'})
    return sorted(result, key=lambda item: (item['price'], item['id']))


def fetch_cpu_offers(provider, key='', reader=_read_json):
    """Cheapest CPU-only Vast.ai offers that meet the minimum above."""
    if provider != 'vast':
        raise ValueError('Moln-CPU stöds bara hos Vast.ai.')
    query = {'limit': 300, 'type': 'on-demand', 'verified': {'eq': True},
             'rentable': {'eq': True}, 'rented': {'eq': False},
             'num_gpus': {'eq': 0}, 'cpu_cores_effective': {'gte': CPU_MIN_CORES},
             'disk_space': {'gte': CPU_MIN_DISK_GB}, 'order': [['dph_total', 'asc']]}
    headers = {'Content-Type': 'application/json', 'User-Agent': 'SubSynq/0.8.153'}
    if key:
        headers['Authorization'] = f'Bearer {key}'
    request = Request('https://console.vast.ai/api/v0/bundles/',
                      data=json.dumps(query).encode('utf-8'), headers=headers, method='POST')
    return normalize_vast_cpu(reader(request))


# A whole-chain GPU rental downloads ~40 GB onto the host, so the host's network decides how
# long the start takes (a measured Vast start spent 31 minutes just installing on a cheap host).
# Hosts are therefore chosen on download speed, not only on price: first >= 500 Mbit/s, then
# >= 300. Whatever GPU model the user picked (RTX 5060 Ti, 3090, 4090, ...) is kept; only the
# host changes. If no host of that model qualifies nothing is rented.
CHAIN_GPU_DOWNLOAD_TIERS = (500, 300)
CHAIN_GPU_MIN_RELIABILITY = 0.95
CHAIN_GPU_MIN_DISK_GB = 80


def fetch_vast_chain_gpu_offers(key, gpu_query, reader=_read_json):
    """Vast.ai offers of ONE GPU model for a whole-chain rental, cheapest first, restricted
    to hosts with a fast network (see CHAIN_GPU_DOWNLOAD_TIERS)."""
    gpu = normalize_gpu_query(gpu_query)
    if not gpu:
        raise ValueError('Ange en GPU-modell, till exempel RTX 3090.')
    headers = {'Content-Type': 'application/json', 'User-Agent': 'SubSynq/0.8.175'}
    if key:
        headers['Authorization'] = f'Bearer {key}'
    for min_download in CHAIN_GPU_DOWNLOAD_TIERS:
        query = {'limit': 400, 'type': 'on-demand', 'verified': {'eq': True},
                 'rentable': {'eq': True}, 'rented': {'eq': False}, 'num_gpus': {'eq': 1},
                 'gpu_name': {'eq': gpu}, 'inet_down': {'gte': min_download},
                 'reliability': {'gte': CHAIN_GPU_MIN_RELIABILITY},
                 'disk_space': {'gte': CHAIN_GPU_MIN_DISK_GB}, 'order': [['dph_total', 'asc']]}
        request = Request('https://console.vast.ai/api/v0/bundles/',
                          data=json.dumps(query).encode('utf-8'), headers=headers, method='POST')
        payload = reader(request)
        offers = payload.get('offers') if isinstance(payload, dict) else None
        if not isinstance(offers, list):
            raise ValueError('Vast.ai skickade inget giltigt erbjudandesvar.')
        # The filters are enforced here as well, never trusted to the server alone.
        good = [offer for offer in offers if isinstance(offer, dict)
                and (offer.get('inet_down') or 0) >= min_download
                and (offer.get('reliability') or 0) >= CHAIN_GPU_MIN_RELIABILITY
                and (offer.get('disk_space') or 0) >= CHAIN_GPU_MIN_DISK_GB]
        result = normalize_vast({'offers': good})
        speeds = {str(offer.get('id')): offer for offer in good}
        for item in result:
            raw = speeds.get(item['id']) or {}
            item['detail'] = (f"{item['detail']} · ner {int(raw.get('inet_down') or 0)} Mbit/s"
                              f" · tillförl. {raw.get('reliability', 0):.2f}")
        if result:
            return result
    return []


def normalize_runpod(payload):
    if not isinstance(payload, dict) or payload.get('errors'):
        raise ValueError('Runpod avvisade katalogfrågan.')
    types = payload.get('data', {}).get('gpuTypes')
    if not isinstance(types, list):
        raise ValueError('Runpod skickade inget giltigt GPU-svar.')
    result = []
    for gpu in types:
        if not isinstance(gpu, dict):
            continue
        price_info = gpu.get('lowestPrice') or {}
        price = _price(price_info.get('uninterruptablePrice'))
        if price is None or price_info.get('stockStatus') not in ('High', 'Medium', 'Low'):
            continue
        available = price_info.get('availableGpuCounts')
        if isinstance(available, list) and 1 not in available:
            continue
        gpu_id = gpu.get('id')
        if not isinstance(gpu_id, str) or not gpu_id:
            continue
        display_name = gpu.get('displayName') or gpu_id
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 80:
            continue
        result.append({'id': gpu_id, 'gpu': display_name,
                       'price': price, 'detail': f"{gpu.get('memoryInGb')} GB VRAM · {price_info.get('stockStatus') or 'lager okänt'}"})
    return sorted(result, key=lambda item: (item['price'], item['gpu']))


# Runpod has no RTX 5060 Ti; a whole-chain GPU rental takes the cheapest
# in-stock NVIDIA card with enough VRAM for Gemma 12B (the gateway re-checks
# the real VRAM and refuses to start jobs on less). CPU pods are priced only
# when created, so the CPU "offer" is a fixed flavor and the price is read
# from the pod afterwards.
RUNPOD_MIN_VRAM_GB = 16
RUNPOD_CPU_FLAVOR = 'cpu3m'
RUNPOD_CPU_VCPUS = 8
RUNPOD_CHAIN_QUERY = ('query { gpuTypes { id displayName memoryInGb secureCloud '
                      'lowestPrice(input: {gpuCount: 1}) { stockStatus uninterruptablePrice '
                      'availableGpuCounts } } }')


def normalize_runpod_chain_gpus(payload):
    if not isinstance(payload, dict) or payload.get('errors'):
        raise ValueError('Runpod avvisade katalogfrågan.')
    types = payload.get('data', {}).get('gpuTypes')
    if not isinstance(types, list):
        raise ValueError('Runpod skickade inget giltigt GPU-svar.')
    result = []
    for gpu in types:
        if not isinstance(gpu, dict):
            continue
        gpu_id, memory = gpu.get('id'), gpu.get('memoryInGb')
        price_info = gpu.get('lowestPrice') or {}
        price = _price(price_info.get('uninterruptablePrice'))
        if not isinstance(gpu_id, str) or not gpu_id.startswith('NVIDIA ') or len(gpu_id) > 80:
            continue
        if not isinstance(memory, (int, float)) or memory < RUNPOD_MIN_VRAM_GB:
            continue
        if gpu.get('secureCloud') is not True or price is None:
            continue
        if price_info.get('stockStatus') not in ('High', 'Medium', 'Low'):
            continue
        counts = price_info.get('availableGpuCounts')
        if isinstance(counts, list) and 1 not in counts:
            continue
        name = str(gpu.get('displayName') or gpu_id)[:60]
        result.append({'id': gpu_id, 'gpu': name, 'price': price, 'vram_gb': float(memory),
                       'detail': f'{memory:g} GB VRAM · Secure Cloud'})
    return sorted(result, key=lambda item: (item['price'], item['gpu']))


def fetch_runpod_chain_offers(key, mode, reader=_read_json):
    """Offers for a whole-chain Runpod rental on 'cpu' or 'cuda'."""
    if mode == 'cpu':
        return [{'id': RUNPOD_CPU_FLAVOR, 'gpu': 'CPU', 'price': None,
                 'detail': f'{RUNPOD_CPU_FLAVOR} · {RUNPOD_CPU_VCPUS} vCPU · priset visas efter start'}]
    if mode != 'cuda':
        raise ValueError('Ogiltigt körsätt.')
    request = Request('https://api.runpod.io/graphql',
                      data=json.dumps({'query': RUNPOD_CHAIN_QUERY}).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'User-Agent': 'SubSynq/0.8.158',
                               'Authorization': f'Bearer {key}'}, method='POST')
    return normalize_runpod_chain_gpus(reader(request))


def fetch_offers(provider, key='', reader=_read_json, gpu_query=''):
    if provider == 'vast':
        query = {'limit': 1000, 'type': 'on-demand', 'verified': {'eq': True},
                 'rentable': {'eq': True}, 'rented': {'eq': False},
                 'num_gpus': {'eq': 1}, 'order': [['dph_total', 'asc']]}
        if gpu_query:
            query['gpu_name'] = {'eq': normalize_gpu_query(gpu_query)}
        headers = {'Content-Type': 'application/json', 'User-Agent': 'SubSynq/0.8.87'}
        if key:
            headers['Authorization'] = f'Bearer {key}'
        request = Request('https://console.vast.ai/api/v0/bundles/',
                          data=json.dumps(query).encode('utf-8'),
                          headers=headers,
                          method='POST')
        return normalize_vast(reader(request))
    if provider == 'runpod':
        query = ('query { gpuTypes { id displayName memoryInGb '
                 'lowestPrice(input: {gpuCount: 1}) { stockStatus uninterruptablePrice availableGpuCounts } } }')
        url = 'https://api.runpod.io/graphql'
        if key:
            url += '?api_key=' + quote(key, safe='')
        request = Request(url,
                          data=json.dumps({'query': query}).encode('utf-8'),
                          headers={'Content-Type': 'application/json',
                                   'User-Agent': 'SubSynq/0.8.87'}, method='POST')
        return normalize_runpod(reader(request))
    raise ValueError('Ogiltig leverantör.')


def catalog(config, provider, *, force=False, gpu_query=''):
    gpu_query = normalize_gpu_query(gpu_query) if provider == 'vast' else ''
    now = time.monotonic()
    cache_key = (provider, gpu_query)
    with _lock:
        cached = _cache.get(cache_key)
        if not force and cached and now - cached['at'] < _MAX_AGE:
            return cached['result']
    try:
        offers = fetch_offers(provider, gpu_query=gpu_query)
        result = {'offers': offers, 'fetched_at': datetime.now(timezone.utc).isoformat(), 'error': None}
    except HTTPError as error:
        message = ('Leverantörens katalog nekade åtkomst.' if error.code in (401, 403)
                   else f'Leverantören svarade med felkod {error.code}.')
        result = {'offers': [], 'fetched_at': None, 'error': message}
    except (URLError, TimeoutError, OSError):
        result = {'offers': [], 'fetched_at': None, 'error': 'Det gick inte att nå leverantören just nu.'}
    except (ValueError, TypeError, KeyError):
        result = {'offers': [], 'fetched_at': None, 'error': 'Leverantörens svar kunde inte läsas.'}
    with _lock:
        _cache[cache_key] = {'at': now, 'result': result}
    return result
