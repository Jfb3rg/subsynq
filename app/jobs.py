"""Persistent subtitle job queue shared by the read-only web UI and AI worker."""
import sqlite3
from datetime import datetime, timezone

ACTIVE = ("queued", "transcribing", "translating", "verifying", "writing",
          "cancel_requested", "delete_queued", "deleting", "restart_queued", "restarting")


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize_jobs(database):
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS subtitle_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_path TEXT NOT NULL,
            media_name TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '',
            output_path TEXT,
            hardware TEXT,
            model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        existing = {row[1] for row in db.execute("PRAGMA table_info(subtitle_jobs)")}
        # The fingerprint the media file had when THIS job wrote its subtitle --
        # compared against a later library scan's own size/mtime to detect a
        # replaced/re-encoded file still sitting under the same filename.
        for column in ("media_size_bytes", "media_mtime_ns"):
            if column not in existing:
                db.execute(f"ALTER TABLE subtitle_jobs ADD COLUMN {column} INTEGER")
        # Set only while a real, billable cloud-GPU instance is rented for
        # this job -- lets a restarted worker find and destroy any instance
        # left running by a crash mid-job, instead of leaking cost forever.
        if "cloud_instance_id" not in existing:
            db.execute("ALTER TABLE subtitle_jobs ADD COLUMN cloud_instance_id INTEGER")
        # The audio track (ffprobe stream index) the user picked by hand; NULL = the worker chooses (English first).
        if "audio_track" not in existing:
            db.execute("ALTER TABLE subtitle_jobs ADD COLUMN audio_track INTEGER")
        # Recreate it because SQLite cannot alter an index predicate in place.
        db.execute("DROP INDEX IF EXISTS one_active_subtitle_job")
        db.execute("""CREATE UNIQUE INDEX one_active_subtitle_job
                    ON subtitle_jobs(media_path)
                    WHERE status IN ('queued','transcribing','translating','verifying','writing',
                                     'cancel_requested','delete_queued','deleting',
                                     'restart_queued','restarting')""")


def enqueue_job(database, media_path, media_name, audio_track=None):
    """Queue a subtitle job. `audio_track` is the audio stream the user picked; None lets the worker choose."""
    stamp = now()
    try:
        with sqlite3.connect(database, timeout=5) as db:
            cursor = db.execute(
                "INSERT INTO subtitle_jobs(media_path,media_name,status,progress,message,created_at,updated_at,"
                "audio_track) VALUES (?,?, 'queued',0,'Väntar på AI-worker',?,?,?)",
                (media_path, media_name, stamp, stamp, audio_track),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError as error:
        raise ValueError("Ett jobb för avsnittet är redan köat eller arbetar.") from error


def enqueue_delete(database, media_path, media_name, subtitle_path):
    stamp = now()
    try:
        with sqlite3.connect(database, timeout=5) as db:
            cursor = db.execute(
                "INSERT INTO subtitle_jobs(media_path,media_name,status,progress,message,output_path,created_at,updated_at) "
                "VALUES (?,?, 'delete_queued',0,'Väntar på säker borttagning',?,?,?)",
                (media_path, media_name, subtitle_path, stamp, stamp),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError as error:
        raise ValueError("Ett jobb för avsnittet är redan köat eller arbetar.") from error


def enqueue_restart(database, media_path, media_name, subtitle_path):
    """Delete the existing AI subtitle and immediately re-run the file, in one
    step -- used when the video itself changed under the same filename."""
    stamp = now()
    try:
        with sqlite3.connect(database, timeout=5) as db:
            # A re-run keeps the audio track the user picked for the earlier run of this file.
            earlier = db.execute("SELECT audio_track FROM subtitle_jobs WHERE media_path=? "
                                 "ORDER BY id DESC LIMIT 1", (media_path,)).fetchone()
            cursor = db.execute(
                "INSERT INTO subtitle_jobs(media_path,media_name,status,progress,message,output_path,created_at,"
                "updated_at,audio_track) "
                "VALUES (?,?, 'restart_queued',0,'Väntar på säker borttagning innan omkörning',?,?,?,?)",
                (media_path, media_name, subtitle_path, stamp, stamp, earlier[0] if earlier else None),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError as error:
        raise ValueError("Ett jobb för avsnittet är redan köat eller arbetar.") from error


def request_cancel(database, job_id, media_path):
    stamp = now()
    with sqlite3.connect(database, timeout=5) as db:
        row = db.execute("SELECT status FROM subtitle_jobs WHERE id=? AND media_path=?",
                         (job_id, media_path)).fetchone()
        if not row or row[0] not in ('queued', 'transcribing', 'translating', 'verifying', 'writing'):
            raise ValueError('Jobbet arbetar inte längre och kan därför inte avbrytas.')
        status = 'cancelled' if row[0] == 'queued' else 'cancel_requested'
        message = ('Avbruten innan bearbetningen startade' if status == 'cancelled'
                   else 'Avbryter efter den pågående AI-delen · ingen SRT kommer att sparas')
        db.execute("UPDATE subtitle_jobs SET status=?,message=?,updated_at=? WHERE id=?",
                   (status, message, stamp, job_id))
    return status


def recent_events(database, page=1, per_page=5):
    page = max(1, page)
    with sqlite3.connect(database, timeout=5) as db:
        db.row_factory = sqlite3.Row
        total = db.execute("SELECT COUNT(*) FROM subtitle_jobs").fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        rows = db.execute(
            "SELECT * FROM subtitle_jobs ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page))
        return [dict(row) for row in rows], page, total_pages


def latest_events(database, limit=30):
    """Newest-first raw feed for the live console -- not paginated."""
    with sqlite3.connect(database, timeout=5) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id,media_name,status,message,updated_at FROM subtitle_jobs "
            "ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]


def latest_jobs(database):
    with sqlite3.connect(database, timeout=5) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT j.* FROM subtitle_jobs j JOIN (
            SELECT media_path, MAX(id) id FROM subtitle_jobs GROUP BY media_path
        ) latest ON latest.id=j.id""")
        return {row["media_path"]: dict(row) for row in rows}
