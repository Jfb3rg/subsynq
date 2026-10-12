"""The banner at the top of every page while the AI models are being downloaded.

The worker (model_manager.py) writes /config/models-status.json; this module only
reads it and draws it. Nothing is shown once every model is in place.
"""
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from i18n import t

STALE_SECONDS = 15 * 60


def load_status(config):
    try:
        data = json.loads((Path(config) / "models-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("models"), list) else None


def needs_banner(status):
    return bool(status) and status.get("state") in ("downloading", "error")


def _age_seconds(status, now=None):
    try:
        stamp = datetime.fromisoformat(status["updated_at"])
    except (KeyError, ValueError, TypeError):
        return 0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - stamp).total_seconds()


def _state_text(model, lang):
    state = model.get("state")
    if state == "present":
        return t("models_state_present", lang)
    if state == "downloading":
        percent = model.get("percent")
        detail = model.get("detail") or ""
        return " · ".join(part for part in (f"{percent} %" if percent is not None else "", detail) if part) \
            or t("models_state_downloading", lang)
    if state == "error":
        return f"{t('models_state_error', lang)}: {model.get('detail') or ''}".rstrip(": ")
    return t("models_state_pending", lang)


def render_models_banner(status, lang="sv", now=None):
    if not needs_banner(status):
        return ""
    models = status.get("models", [])
    missing_gb = sum(m.get("size_gb", 0) for m in models if m.get("state") != "present")
    error = status.get("state") == "error"
    rows = []
    for model in models:
        percent = model.get("percent")
        width = 100 if model.get("state") == "present" else (percent or 0)
        colour = "var(--dg)" if model.get("state") == "error" else "var(--ac)"
        rows.append(
            f"<tr data-model='{escape(str(model.get('id', '')), quote=True)}'>"
            f"<td>{escape(str(model.get('label', '')))}</td>"
            f"<td class='muted'>{escape(str(model.get('used_by', '')))}</td>"
            f"<td style='min-width:140px'><div class='meter-track'><div class='meter-fill' data-model-fill "
            f"style='width:{int(width)}%;background:{colour};'></div></div></td>"
            f"<td data-model-state>{escape(_state_text(model, lang))}</td></tr>")
    stale = _age_seconds(status, now) > STALE_SECONDS
    notes = ""
    if status.get("message"):
        notes += f"<p class='{'error' if error else 'muted'}'>{escape(str(status['message']))}</p>"
    if error:
        notes += f"<p class='muted'>{escape(t('models_retry', lang))}</p>"
    if stale:
        notes += f"<p class='error'>{escape(t('models_stale', lang))}</p>"
    title = t("models_title_error" if error else "models_title_downloading", lang)
    attributes = " ".join(
        f"data-t-{key}='{escape(t('models_state_' + key, lang), quote=True)}'"
        for key in ("present", "pending", "downloading", "error"))
    return (
        f"<div class='card models-card' data-models-card data-state='{escape(str(status.get('state')), quote=True)}' "
        f"{attributes}><h2>{escape(title)} · "
        f"{escape(t('models_count', lang).format(done=status.get('done', 0), total=status.get('total', len(models))))}</h2>"
        f"<p class='muted'>{escape(t('models_intro', lang).format(gb=round(missing_gb)))}</p>"
        f"<table><tbody>{''.join(rows)}</tbody></table>{notes}</div>")


_REQUEST_NOTICE = {"1": "models_remove_requested", "remove": "models_remove_requested",
                   "update": "models_update_requested", "check": "models_check_requested"}


def _version_text(model, lang):
    update = model.get("update") or ""
    if update == "available":
        return f"<span class='subtitle-status working'>{escape(t('models_version_available', lang))}</span>"
    if update == "current":
        return escape(t("models_version_current", lang))
    return f"<span class='muted'>{escape(t('models_version_unknown', lang))}</span>"


def render_models_page(status, token, lang="sv", requested=""):
    """Settings > Models: what SubSynq uses, newer versions, and old models that can be removed.

    Nothing here happens by itself: updating and removing both need an explicit click.
    """
    if requested is True:
        requested = "1"
    heading = f"<h2>{escape(t('models_page_heading', lang))}</h2>"
    if not status:
        return (f"<div class='card'>{heading}<p class='muted'>{escape(t('models_page_no_status', lang))}</p></div>")
    models = status.get("models", [])
    rows = "".join(
        f"<tr><td>{escape(str(m.get('label', '')))}<br><small class='muted'>{escape(str(m.get('name', '')))}</small></td>"
        f"<td class='muted'>{escape(str(m.get('used_by', '')))}</td>"
        f"<td>{escape(str(m.get('size_gb', '')))} GB</td><td>{escape(_state_text(m, lang))}</td>"
        f"<td>{_version_text(m, lang)}</td></tr>"
        for m in models)
    errors = "".join(f"<p class='error'>{escape(t('models_catalogue_error', lang).format(error=e))}</p>"
                     for e in status.get("catalogue_errors", []))
    notices = ""
    if requested in _REQUEST_NOTICE:
        notices += f"<p class='success' role='status'>{escape(t(_REQUEST_NOTICE[requested], lang))}</p>"
    if status.get("notice"):
        notices += f"<p class='success' role='status'>{escape(str(status['notice']))}</p>"
    listed = (
        f"<div class='card'>{heading}"
        f"<p class='muted'>{escape(t('models_page_intro', lang))}</p>"
        f"<p class='muted'>{escape(t('models_page_source', lang).format(path=status.get('source') or '–'))}</p>"
        f"{errors}{notices}"
        f"<table><thead><tr><th>{escape(t('models_col_model', lang))}</th><th>{escape(t('models_col_used_by', lang))}"
        f"</th><th>{escape(t('models_col_size', lang))}</th><th>{escape(t('models_col_state', lang))}</th>"
        f"<th>{escape(t('models_col_version', lang))}</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>")

    # --- newer versions
    available = [m for m in models if m.get("update") == "available"]
    checked = status.get("update_checked_at") or ""
    token_field = f"<input type='hidden' name='token' value='{escape(token, quote=True)}'>"
    check_form = ("<form action='/models/update' method='post' style='display:inline'>" + token_field
                  + "<input type='hidden' name='check' value='1'>"
                  f"<button type='submit' class='small-button'>{escape(t('models_check_button', lang))}</button></form>")
    head = (f"<div class='card'><h2>{escape(t('models_update_heading', lang))}</h2>"
            f"<p class='muted'>{escape(t('models_update_intro', lang))}</p>"
            f"<p class='muted'>{escape(t('models_update_checked', lang).format(when=checked.replace('T', ' ')[:16] or t('models_update_never', lang)))}"
            f" {check_form}</p>")
    if not available:
        update = head + (f"<p class='muted'>{escape(t('models_update_none', lang))}</p>" if checked else "") + "</div>"
    else:
        boxes = "".join(
            f"<tr><td><input type='checkbox' name='key' value='{escape(str(m.get('kind', '')) + '|' + str(m.get('name', '')), quote=True)}' "
            f"aria-label='{escape(str(m.get('label', '')), quote=True)}'></td>"
            f"<td>{escape(str(m.get('label', '')))}<br><small class='muted'>{escape(str(m.get('name', '')))}</small></td>"
            f"<td class='muted'>{escape(str(m.get('installed', '')))} → {escape(str(m.get('latest', '')))}</td></tr>"
            for m in available)
        confirm = escape(t("models_update_confirm", lang), quote=True)
        ollama_note = (f"<p class='muted'>{escape(t('models_update_ollama_note', lang))}</p>"
                       if any(m.get("kind") == "ollama" for m in available) else "")
        update = (head + "<form action='/models/update' method='post'>" + token_field
                  + f"<table><tbody>{boxes}</tbody></table>{ollama_note}"
                  f"<p><button type='submit' class='small-button' onclick=\"return confirm('{confirm}')\">"
                  f"{escape(t('models_update_button', lang))}</button></p></form></div>")

    # --- old models
    obsolete = status.get("obsolete", [])
    if not obsolete:
        old = (f"<div class='card'><h2>{escape(t('models_obsolete_heading', lang))}</h2>"
               f"<p class='muted'>{escape(t('models_obsolete_none', lang))}</p></div>")
    else:
        gb = sum(float(m.get("size_gb", 0)) for m in obsolete)
        boxes = "".join(
            f"<tr><td><input type='checkbox' name='key' value='{escape(str(m.get('key', '')), quote=True)}' "
            f"aria-label='{escape(str(m.get('label', '')), quote=True)}'></td>"
            f"<td>{escape(str(m.get('label', '')))}<br><small class='muted'>{escape(str(m.get('name', '')))}</small></td>"
            f"<td>{escape(str(m.get('size_gb', '')))} GB</td></tr>" for m in obsolete)
        confirm = escape(t("models_remove_confirm", lang), quote=True)
        old = (f"<div class='card'><h2>{escape(t('models_obsolete_heading', lang))}</h2>"
               f"<p class='muted'>{escape(t('models_obsolete_intro', lang).format(gb=round(gb, 1)))}</p>"
               "<form action='/models/remove' method='post'>" + token_field
               + f"<table><tbody>{boxes}</tbody></table>"
               f"<p><button type='submit' class='danger small-button' onclick=\"return confirm('{confirm}')\">"
               f"{escape(t('models_remove_button', lang))}</button></p></form></div>")
    return listed + update + old
