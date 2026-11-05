from django.core.management.base import BaseCommand

from reminders.jobs import send_reminders


class Command(BaseCommand):
    help = "Remind the customers whose invoices are overdue"

    def handle(self, *args: object, **options: object) -> None:
        self.stdout.write(f"{send_reminders()} reminded")
