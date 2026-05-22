import os
import sys

from django.apps import AppConfig

# Management commands that should not start the background scheduler
_SKIP_COMMANDS = {
    "migrate", "makemigrations", "collectstatic", "createsuperuser",
    "shell", "test", "check", "showmigrations", "sqlmigrate",
    "fill_company_domains", "revalidate_submissions", "revalidate_all", "resolve_flagged",
    "add_general_category", "delete_test_students",
    "seed_reports", "seed_leaderboard", "boost_platform_data", "cleanup_and_trim",
}


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        # Never start during one-off management commands
        if len(sys.argv) > 1 and sys.argv[1] in _SKIP_COMMANDS:
            return

        # When Django's autoreloader is active (runserver without --noreload),
        # it spawns a child process with RUN_MAIN=true.  Skip the outer watcher
        # process so the scheduler only runs once.
        using_reloader = (
            "runserver" in sys.argv and "--noreload" not in sys.argv
        )
        if using_reloader and os.environ.get("RUN_MAIN") != "true":
            return  # outer reloader process — skip

        from .scheduler import start
        start()
