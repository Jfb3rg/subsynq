"""Read-only registry checks before any billable cloud request."""
import hashlib
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener


def verify_image(image):
    name, digest = image.rsplit('@', 1)
    registry, repository = name.split('/', 1)
    if registry == 'docker.io':
        registry = 'registry-1.docker.io'
    base = f'https://{registry}/v2/{repository}'
    opener = build_opener()
    headers = {'Accept': ', '.join(('application/vnd.oci.image.manifest.v1+json',
               'application/vnd.docker.distribution.manifest.v2+json',
               'application/vnd.oci.image.index.v1+json',
               'application/vnd.docker.distribution.manifest.list.v2+json'))}

    def get(path, expected):
        request = Request(base + path, headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except HTTPError as error:
            if error.code != 401 or 'Authorization' in headers:
                raise
            challenge = error.headers.get('WWW-Authenticate', '')
            if not challenge.lower().startswith('bearer '):
                raise
            fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            realm = fields.get('realm', '')
            if not realm.startswith('https://'):
                raise ValueError('Registrets token-adress måste använda HTTPS.')
            query = urlencode({'service': fields.get('service', ''),
                               'scope': f'repository:{repository}:pull'})
            with opener.open(realm + '?' + query, timeout=15) as token_response:
                token = json.load(token_response)
            headers['Authorization'] = 'Bearer ' + (token.get('token') or token['access_token'])
            response = opener.open(Request(base + path, headers=headers), timeout=15)
        with response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or 'sha256:' + hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Fel digest eller för stor image-metadata.')
        return json.loads(data)

    manifest = get('/manifests/' + digest, digest)
    if 'manifests' in manifest:
        descriptor = next(item for item in manifest['manifests']
                          if item.get('platform', {}).get('os') == 'linux'
                          and item.get('platform', {}).get('architecture') == 'amd64')
        digest = descriptor['digest']
        manifest = get('/manifests/' + digest, digest)
    config_digest = manifest['config']['digest']
    config = get('/blobs/' + config_digest, config_digest)
    if (config.get('os') != 'linux' or config.get('architecture') != 'amd64'
            or config.get('config', {}).get('Labels', {}).get('org.subsynq.cloud.prepared') != '1'):
        raise ValueError('Imagen är inte en färdig SubSynq-molnimage för Linux/amd64.')
    return True
