"""Validated, user-visible hardware state shared by the launcher and web UI."""
import json
import re
from html import escape

from i18n import t

DEVICE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Providers that can rent a CPU-only server running the whole AI chain.
CLOUD_CPU_PROVIDERS = ("vast", "runpod")
# Names shown to the user; the ids stay the stored provider keys.
PROVIDER_NAMES = {"vast": "Vast.ai", "runpod": "Runpod.io"}

DEFAULT = {
    "updated_at": None,
    "worker_status": "not_connected",
    "active": None,
    "recommended": "cpu",
    "available": [
        {"id": "cpu", "name": "CPU", "backend": "CPU", "verified": True, "available": True}
    ],
    "fallback_reason": "Ingen AI-motor är ansluten ännu.",
    "worker_error": None,
    "asr_model": "Inte startad",
    "resources": None,
    "gpu_resources": None,
}


def load_hardware(config):
    path = config / "hardware-status.json"
    if not path.is_file():
        return dict(DEFAULT)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        devices = []
        for item in raw.get("available", [])[:16]:
            device_id = str(item.get("id", ""))
            if not DEVICE_ID.fullmatch(device_id):
                continue
            devices.append({
                "id": device_id,
                "name": str(item.get("name", device_id))[:120],
                "backend": str(item.get("backend", "Okänd"))[:80],
                "verified": bool(item.get("verified", False)),
                "available": bool(item.get("available", False)),
            })
        if not any(item["id"] == "cpu" for item in devices):
            devices.append(dict(DEFAULT["available"][0]))
        active = raw.get("active")
        active = str(active) if active and any(item["id"] == str(active) for item in devices) else None
        recommended = str(raw.get("recommended", "cpu"))
        if not any(item["id"] == recommended and item["available"] for item in devices):
            recommended = "cpu"
        resources = None
        raw_resources = raw.get("resources")
        if isinstance(raw_resources, dict):
            try:
                cpu = float(raw_resources.get("cpu_percent"))
                ram_used = float(raw_resources.get("ram_used_gb"))
                ram_total = float(raw_resources.get("ram_total_gb"))
                if 0 <= cpu <= 100 and 0 <= ram_used <= ram_total <= 10000:
                    resources = {"cpu_percent": round(cpu, 1), "ram_used_gb": round(ram_used, 1),
                                "ram_total_gb": round(ram_total, 1)}
            except (TypeError, ValueError):
                resources = None
        gpu_resources = None
        raw_gpu_resources = raw.get("gpu_resources")
        if isinstance(raw_gpu_resources, dict):
            try:
                vram_used = float(raw_gpu_resources.get("vram_used_gb"))
                vram_total = float(raw_gpu_resources.get("vram_total_gb"))
                if 0 <= vram_used <= vram_total <= 512:
                    gpu_resources = {"vram_used_gb": round(vram_used, 1),
                                     "vram_total_gb": round(vram_total, 1)}
            except (TypeError, ValueError):
                gpu_resources = None
        return {
            "updated_at": str(raw.get("updated_at"))[:40] if raw.get("updated_at") else None,
            "worker_status": str(raw.get("worker_status", "not_connected"))[:40],
            "active": active,
            "recommended": recommended,
            "available": devices,
            "fallback_reason": str(raw.get("fallback_reason"))[:240] if raw.get("fallback_reason") else None,
            "worker_error": str(raw.get("worker_error"))[:240] if raw.get("worker_error") else None,
            "asr_model": str(raw.get("asr_model", "Inte startad"))[:120],
            "resources": resources,
            "gpu_resources": gpu_resources,
        }
    except (OSError, ValueError, TypeError, AttributeError):
        result = dict(DEFAULT)
        result["fallback_reason"] = "Hårdvarustatusen kunde inte läsas; CPU visas som säker reserv."
        return result


def allowed_preferences(hardware, cloud_providers=()):
    return {"auto", *(item["id"] for item in hardware["available"] if item["available"]),
            *(f"cloud-{provider}" for provider in cloud_providers),
            *(f"cloud-cpu-{provider}" for provider in cloud_providers if provider in CLOUD_CPU_PROVIDERS)}


def render_hardware(hardware, preference, token, saved=False, return_to="/", lang="sv",
                     cloud_providers=(), cloud_profile=None):
    devices = {item["id"]: item for item in hardware["available"]}

    def gpu_label(provider):
        # Runpod has no RTX 5060 Ti: it rents its cheapest in-stock NVIDIA card (>= 16 GB).
        if provider == "runpod":
            return t("cloud_gpu_runpod_auto", lang)
        return cloud_profile["gpu"] if cloud_profile else t("cloud_gpu_generic", lang)

    def cloud_device_label(device_id):
        if (device_id and device_id.startswith("cloud-cpu-")
                and device_id[len("cloud-cpu-"):] in CLOUD_CPU_PROVIDERS):
            return t("cloud_cpu_option_label", lang).format(provider=PROVIDER_NAMES.get(device_id[len("cloud-cpu-"):], device_id[len("cloud-cpu-"):]))
        if device_id and device_id.startswith("cloud-") and device_id[6:] in cloud_providers:
            gpu_name = gpu_label(device_id[6:])
            return t("cloud_gpu_option_label", lang).format(gpu=gpu_name, provider=PROVIDER_NAMES.get(device_id[6:], device_id[6:]))
        return None

    active = devices.get(hardware["active"])
    active_cloud_label = cloud_device_label(hardware["active"])
    active_text = (f'{active["name"]} · {active["backend"]}' if active
                   else active_cloud_label if active_cloud_label
                   else t("no_ai_processing_active", lang))
    cloud_label = cloud_device_label(preference)
    target = devices[hardware["recommended"]] if preference == "auto" else devices.get(preference)
    preference_text = (t("automatic_arrow", lang).format(name=target["name"]) if preference == "auto" and target
                       else cloud_label if cloud_label
                       else target["name"] if target else t("automatic", lang))
    options = [("auto", t("automatic_recommends", lang).format(name=devices[hardware["recommended"]]["name"]))]
    options.extend((item["id"], f'{item["name"]} · {item["backend"]}' +
                    f' · {t("verified_suffix", lang) if item["verified"] else t("not_verified_suffix", lang)}')
                   for item in hardware["available"] if item["available"])
    for provider in ("vast", "runpod"):
        if provider in cloud_providers:
            gpu_name = gpu_label(provider)
            options.append((f"cloud-{provider}", t("cloud_gpu_option_label", lang).format(
                gpu=gpu_name, provider=PROVIDER_NAMES.get(provider, provider))))
            if provider in CLOUD_CPU_PROVIDERS:
                options.append((f"cloud-cpu-{provider}",
                                t("cloud_cpu_option_label", lang).format(provider=PROVIDER_NAMES.get(provider, provider))))
    option_html = "".join(
        f'<option value="{escape(key)}"{" selected" if key == preference else ""}>{escape(label)}</option>'
        for key, label in options
    )
    if "vast" not in cloud_providers:
        option_html += f'<option value="cloud-vast" disabled>{escape(t("cloud_gpu_vast_missing", lang))}</option>'
        option_html += f'<option value="cloud-cpu-vast" disabled>{escape(t("cloud_cpu_vast_missing", lang))}</option>'
    if "runpod" not in cloud_providers:
        option_html += f'<option value="cloud-runpod" disabled>{escape(t("cloud_gpu_runpod_missing", lang))}</option>'
        option_html += f'<option value="cloud-cpu-runpod" disabled>{escape(t("cloud_cpu_runpod_missing", lang))}</option>'
    ready = [item["name"] for item in hardware["available"] if item["verified"] and item["available"]]
    waiting = [item["name"] for item in hardware["available"] if item["verified"] and not item["available"]]
    reason = (f'<p class="warning"><strong>{escape(t("clear_fallback_reason", lang))}:</strong> '
             f'{escape(hardware["fallback_reason"])}</p>' if hardware["fallback_reason"] else "")
    worker_error = (f'<p class="warning"><strong>{escape(t("latest_job_error", lang))}:</strong> '
                    f'{escape(hardware["worker_error"])}</p>' if hardware.get("worker_error") else "")
    state_labels = {
        "not_connected": t("state_not_connected", lang),
        "verifierad_idle": t("state_waiting_for_job", lang),
        "ready_idle": t("state_waiting_for_job", lang),
        "running": t("working", lang),
        "error": t("state_error", lang),
    }
    state_label = state_labels.get(hardware["worker_status"], hardware["worker_status"].replace("_", " "))
    saved_notice = f'<p class="success" role="status">{escape(t("hardware_saved", lang))}</p>' if saved else ''
    signature = hardware["recommended"] + ';' + ';'.join(sorted(
        item["id"] for item in hardware["available"] if item["available"]
    ))
    return (
        f'<section class="hardware" data-hardware-signature="{escape(signature, quote=True)}">'
        f'<div class="section-heading"><div><h2>{escape(t("ai_hardware_heading", lang))}</h2>'
        f'<p class="active-device"><strong>{escape(t("active_now", lang))}:</strong> {escape(active_text)}</p></div>'
        f'<span class="state">{escape(state_label)}</span></div>'
        f'{saved_notice}<p><strong>{escape(t("saved_choice_for_next_job", lang))}:</strong> {escape(preference_text)}</p>'
        f'<p><strong>{escape(t("usable_by_next_job", lang))}:</strong> {escape(", ".join(ready) or t("none", lang))}</p>'
        + (f'<p><strong>{escape(t("verified_not_connected_yet", lang))}:</strong> {escape(", ".join(waiting))}</p>'
           if waiting else '') +
        f'<p><strong>{escape(t("asr_model", lang))}:</strong> {escape(hardware["asr_model"])}</p>{reason}{worker_error}'
        f'<form action="/hardware" method="post" class="hardware-form" '
        f'onsubmit="var v=this.preference.value; return v.indexOf(\'cloud-\')!==0 || '
        f'confirm(v.indexOf(\'cloud-cpu-\')===0 ? \'{escape(t("cloud_cpu_confirm", lang), quote=True)}\' : '
        f'\'{escape(t("cloud_gpu_confirm", lang), quote=True)}\');">'
        f'<input type="hidden" name="token" value="{escape(token)}">'
        f'<input type="hidden" name="return" value="{escape(return_to, quote=True)}">'
        f'<label for="hardware-preference">{escape(t("desired_device", lang))}</label>'
        f'<select id="hardware-preference" name="preference" '
        f'onchange="this.form.dataset.saving=\'1\'; this.form.requestSubmit();">{option_html}</select>'
        f'<button type="submit">{escape(t("save_choice", lang))}</button></form>'
        f'<p class="muted">{escape(t("hardware_form_note", lang))}</p></section>'
    )
