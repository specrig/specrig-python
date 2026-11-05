import specrig.django
from specrig import Backend, Postgres, Redis


class Harness(specrig.django.Harness):
    service = "reminders"
    settings = "config.settings"

    billing = Backend(
        "https://billing.reminders.test", setting="BILLING_URL", contract="../docs/billing.yaml", failure="Error"
    )
    database = Postgres("DATABASE_URL")
    cache = Redis("REDIS_URL")

    constants = {"SECRET_KEY": "specrig-reminders-secret-key-for-the-cases-only"}
