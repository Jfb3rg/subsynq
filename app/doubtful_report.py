"""The rows a finished subtitle keeps although the final review still doubted them (written by the worker, listed in the GUI)."""
import json
from html import escape
from pathlib import Path

from i18n import t


def report_path(config_dir, job_id):
    return Path(config_dir) / 'doubtful-lines' / f'job-{int(job_id)}.json'


def load_report(config_dir, job_id):
    """The saved report of a job, or None when there is none (or it cannot be read)."""
    try:
        data = json.loads(report_path(config_dir, job_id).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get('rows'), list) else None


def has_report(config_dir, job_id):
    try:
        return report_path(config_dir, job_id).is_file()
    except (OSError, ValueError, TypeError):
        return False


def clock(seconds):
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return ''
    return f'{total // 60:02d}:{total % 60:02d}'


def report_link(job_id, lang='sv'):
    return f"<a href='/report?job={int(job_id)}'>{escape(t('doubtful_link', lang))}</a>"


def render_report_content(report, lang='sv'):
    """Stacked blocks (not a table), so every row reads the same on a phone as on a screen."""
    if not report:
        return f"<div class='card'><h2>{escape(t('doubtful_heading', lang))}</h2><p class='muted'>{escape(t('doubtful_missing', lang))}</p></div>"
    blocks = []
    for row in report['rows']:
        blocks.append(
            "<div class='doubtful-row' style='padding:12px 0;border-top:1px solid var(--bd);overflow-wrap:anywhere'>"
            f"<strong>{escape(clock(row.get('start')))}</strong> <small class='muted'>#{escape(str(row.get('id', '')))}</small>"
            f"<p style='margin:6px 0 2px'><small class='muted'>{escape(t('doubtful_source', lang))}</small><br>{escape(str(row.get('source', '')))}</p>"
            f"<p style='margin:6px 0 2px'><small class='muted'>{escape(t('doubtful_swedish', lang))}</small><br>{escape(str(row.get('sv', '')))}</p>"
            f"<p style='margin:6px 0 2px'><small class='muted'>{escape(t('doubtful_remark', lang))}</small><br>{escape(str(row.get('reason', '')))}</p>"
            + (f"<p style='margin:6px 0 2px'><small class='muted'>{escape(t('doubtful_cut_off', lang))}</small></p>" if row.get('source_cut_off') else '')
            + "</div>")
    return (f"<div class='card'><h2>{escape(t('doubtful_heading', lang))}</h2>"
            f"<p class='muted'>{escape(str(report.get('media_name', '')))}</p>"
            f"<p class='muted'>{escape(t('doubtful_intro', lang).format(count=len(report['rows'])))}</p>"
            f"{''.join(blocks)}"
            f"<p><a href='/queue'>{escape(t('doubtful_back', lang))}</a></p></div>")
