import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django.utils import timezone

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="Asia/Riyadh")
_started = False

JOB_ID = "fetch_opportunities_auto"
MONITOR_JOB_ID = "monitor_opportunities_auto"


def _run_scheduled_fetch():
    from .models import Admin, AuditLog, FetchSchedule
    from .opportunity_observer import run_fetch_session

    schedule = FetchSchedule.objects.first()
    if not schedule or not schedule.enabled:
        return

    # Use the first admin account as the initiator
    admin = Admin.objects.first()
    if not admin:
        logger.warning("Scheduled fetch: no admin account found, skipping.")
        return

    logger.info("Scheduled fetch starting...")
    try:
        session = run_fetch_session(admin)
        AuditLog.objects.create(
            admin=admin,
            action="scheduled_fetch",
            target_type="fetch_session",
            target_id=session.id,
            target_label=f"Session #{session.id}",
            note=(
                f"Auto-scheduled run. "
                f"Found {session.total_found}, "
                f"Added {session.total_added}, "
                f"Skipped {session.total_skipped}"
            ),
        )
        schedule.last_run = timezone.now()
        _update_next_run(schedule)
        schedule.save(update_fields=["last_run", "next_run"])
        logger.info(
            "Scheduled fetch finished. Added %d opportunities.", session.total_added
        )
    except Exception:
        logger.exception("Scheduled fetch failed.")


def _update_next_run(schedule):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    riyadh = ZoneInfo("Asia/Riyadh")
    now = timezone.now().astimezone(riyadh)
    t = schedule.run_at_time
    if schedule.frequency == "daily":
        candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        schedule.next_run = candidate
    else:
        dow = schedule.day_of_week or 0
        days_ahead = (dow - now.weekday()) % 7 or 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=t.hour, minute=t.minute, second=0, microsecond=0
        )
        schedule.next_run = candidate


def _schedule_job(schedule):
    t = schedule.run_at_time
    if schedule.frequency == "daily":
        trigger = CronTrigger(hour=t.hour, minute=t.minute, timezone="Asia/Riyadh")
    else:
        dow = schedule.day_of_week or 0
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        trigger = CronTrigger(
            day_of_week=day_names[dow],
            hour=t.hour,
            minute=t.minute,
            timezone="Asia/Riyadh",
        )
    _scheduler.add_job(
        _run_scheduled_fetch,
        trigger=trigger,
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Fetch job scheduled: %s at %02d:%02d", schedule.frequency, t.hour, t.minute)


def _ensure_running():
    """Start the BackgroundScheduler if it hasn't been started yet."""
    if not _scheduler.running:
        _scheduler.start()
        logger.info("APScheduler started.")


def reload_schedule():
    """Read FetchSchedule from DB and (re-)register the APScheduler job.

    Safe to call at any time — starts the scheduler lazily if needed so that
    calling this from a web request always works even when start() was skipped
    (e.g. --noreload or WSGI deployment).
    """
    from .models import FetchSchedule

    _ensure_running()

    schedule = FetchSchedule.objects.first()
    if not schedule or not schedule.enabled:
        if _scheduler.get_job(JOB_ID):
            _scheduler.remove_job(JOB_ID)
            logger.info("Fetch job removed (scheduling disabled).")
        return
    _schedule_job(schedule)


def _run_scheduled_monitor():
    from .models import (
        Admin, AuditLog, MonitorResult, MonitorSchedule, MonitorSession,
        Opportunity, OpportunityStatus,
    )
    from .link_validator import validate_submission as link_validate_submission

    schedule = MonitorSchedule.objects.first()
    if not schedule or not schedule.enabled:
        return

    admin = Admin.objects.first()
    if not admin:
        logger.warning("Scheduled monitor: no admin account found, skipping.")
        return

    logger.info("Scheduled monitor starting...")
    try:
        open_opps = list(
            Opportunity.objects
            .filter(status=OpportunityStatus.OPEN)
            .exclude(source_link__isnull=True)
            .exclude(source_link="")
        )
        total = len(open_opps)

        # Create the session immediately so it appears in the UI right away
        session_obj = MonitorSession.objects.create(checked=total, auto_closed=0, errors=0)
        logger.info("Scheduled monitor: session #%d created, checking %d opportunities.", session_obj.id, total)

        db_results = []
        for opp in open_opps:
            decision = "error"
            reason = "Validation error — could not reach the validator."
            try:
                result = link_validate_submission(
                    url=opp.source_link,
                    title=opp.title or "",
                    company=opp.company or "",
                    description=opp.description or "",
                    skip_to_step=3,
                    stop_after_step=3,
                )
                fs = result["final_status"]
                step3 = (result.get("steps") or {}).get("step3_availability") or {}
                reason = step3.get("detail") or ""
                if fs == "rejected":
                    opp.status = OpportunityStatus.CLOSED
                    opp.save(update_fields=["status"])
                    AuditLog.objects.create(
                        admin=admin,
                        action="auto_closed",
                        target_type="Opportunity",
                        target_id=opp.pk,
                        target_label=opp.title,
                        note="Opportunity auto-closed by Status Monitor (scheduled)",
                    )
                    decision = "auto_closed"
                    reason = reason or "Opportunity appears closed."
                else:
                    decision = "still_open"
                    reason = reason or "Opportunity still available."
            except Exception as exc:
                reason = f"Error: {exc}"
            db_results.append({"opp": opp, "decision": decision, "reason": reason})

        closed = sum(1 for r in db_results if r["decision"] == "auto_closed")
        errors = sum(1 for r in db_results if r["decision"] == "error")

        # Update the session with final counts
        session_obj.auto_closed = closed
        session_obj.errors = errors
        session_obj.save(update_fields=["auto_closed", "errors"])

        MonitorResult.objects.bulk_create([
            MonitorResult(
                session=session_obj,
                opportunity=r["opp"],
                ai_decision=r["decision"],
                reason=r["reason"],
            )
            for r in db_results
        ])
        AuditLog.objects.create(
            admin=admin,
            action="scheduled_monitor",
            target_type="MonitorSession",
            target_id=session_obj.id,
            target_label=f"Session #{session_obj.id}",
            note=(
                f"Auto-scheduled monitor run. "
                f"Checked {total}, "
                f"auto-closed {closed}, "
                f"errors {errors}"
            ),
        )
        schedule.last_run = timezone.now()
        _update_monitor_next_run(schedule)
        schedule.save(update_fields=["last_run", "next_run"])
        logger.info(
            "Scheduled monitor finished. Checked %d, closed %d.", total, closed
        )
    except Exception:
        logger.exception("Scheduled monitor failed.")


def _update_monitor_next_run(schedule):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    riyadh = ZoneInfo("Asia/Riyadh")
    now = timezone.now().astimezone(riyadh)
    t = schedule.run_at_time
    if schedule.frequency == "daily":
        candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        schedule.next_run = candidate
    else:
        dow = schedule.day_of_week or 0
        days_ahead = (dow - now.weekday()) % 7 or 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=t.hour, minute=t.minute, second=0, microsecond=0
        )
        schedule.next_run = candidate


def _schedule_monitor_job(schedule):
    t = schedule.run_at_time
    if schedule.frequency == "daily":
        trigger = CronTrigger(hour=t.hour, minute=t.minute, timezone="Asia/Riyadh")
    else:
        dow = schedule.day_of_week or 0
        day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        trigger = CronTrigger(
            day_of_week=day_names[dow],
            hour=t.hour,
            minute=t.minute,
            timezone="Asia/Riyadh",
        )
    _scheduler.add_job(
        _run_scheduled_monitor,
        trigger=trigger,
        id=MONITOR_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(
        "Monitor job scheduled: %s at %02d:%02d", schedule.frequency, t.hour, t.minute
    )


def reload_monitor_schedule():
    """Read MonitorSchedule from DB and (re-)register the APScheduler job."""
    from .models import MonitorSchedule

    _ensure_running()

    schedule = MonitorSchedule.objects.first()
    if not schedule or not schedule.enabled:
        if _scheduler.get_job(MONITOR_JOB_ID):
            _scheduler.remove_job(MONITOR_JOB_ID)
            logger.info("Monitor job removed (scheduling disabled).")
        return
    _schedule_monitor_job(schedule)


def start():
    global _started
    if _started:
        return
    _started = True

    _ensure_running()

    try:
        reload_schedule()
    except Exception:
        logger.exception("Could not load fetch schedule on startup.")

    try:
        reload_monitor_schedule()
    except Exception:
        logger.exception("Could not load monitor schedule on startup.")
