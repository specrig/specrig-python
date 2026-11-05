import specrig.fastapi
from specrig import Backend, Postgres


class Harness(specrig.fastapi.Harness):
    service = "shop"
    app = "shop.main:app"

    billing = Backend(
        "https://billing.shop.test", setting="BILLING_URL", contract="../docs/billing.yaml", failure="Error"
    )
    database = Postgres("DATABASE_URL", scheme="postgresql+psycopg")
