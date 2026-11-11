import shutil
from pathlib import Path

import pytest
import tomlkit

from specrig.tool import agents, cli
from specrig.tool.init import init

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = "Read @specrig.md before the first edit: Rule #11, and how this service's suite runs."


def copy(fixture: str, tmp_path: Path) -> Path:
    target = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, target)
    return target


def test_init_lays_the_harness_out_from_what_the_code_reads(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)

    code, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL", "--contract", "crm=../docs/crm.yaml"], Path("/"))

    assert code == 0, out
    assert (dir / "tests/internal/harness.py").read_text() == (
        "import specrig.fastapi\n"
        "from specrig import Backend, Postgres, Redis\n"
        "\n"
        "\n"
        "class Harness(specrig.fastapi.Harness):\n"
        '    service = "gateway"\n'
        '    app = "gateway.main:app"\n'
        "\n"
        '    crm = Backend("https://crm.gateway.test", setting="GATEWAY_CRM_URL", contract="../docs/crm.yaml")\n'
        '    database = Postgres("DATABASE_URL", scheme="postgresql+asyncpg")\n'
        '    cache = Redis("REDIS_URL")\n'
        "\n"
        '    constants = {"CRM_TOKEN": ""}\n'
    )
    assert (
        dir / "tests/internal/test_cases.py"
    ).read_text() == 'from pytest_bdd import scenarios\n\nscenarios("../features", "boot.feature")\n'
    assert (dir / "tests/internal/boot.feature").is_file()
    assert (dir / "tests/features").is_dir()
    assert (dir / "tests/steps").is_dir()
    assert (dir / "CLAUDE.md").readlink() == Path("AGENTS.md")
    assert (dir / "tests/surface.yaml").is_file()
    assert (dir / "tests/checks.yaml").is_file()
    assert (dir / "specrig.md").is_file()
    assert not (dir / "Makefile").exists()


def test_init_names_the_tool_of_a_virtual_environment_outside_the_repository_as_the_services_own(
    tmp_path: Path,
) -> None:
    dir = copy("fastapi", tmp_path)

    _, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert (dir / "specrig.yaml").read_text() == "guard: [.venv/bin/specrig]\n"
    assert "specrig.yaml names .venv/bin/specrig: the plugin runs it, so install specrig in the service's .venv" in out


def test_init_adds_the_harness_to_the_dev_dependency_group(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)

    _, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    pyproject = tomlkit.parse((dir / "pyproject.toml").read_text())
    assert pyproject["dependency-groups"]["dev"] == ["specrig[fastapi,postgres]>=0.1"]
    assert pyproject["project"]["name"] == "gateway"
    assert "~ pyproject.toml: specrig[fastapi,postgres]>=0.1 in [dependency-groups] dev" in out


def test_a_django_service_is_set_up_by_its_settings_module_with_a_secret_key_of_its_own(tmp_path: Path) -> None:
    dir = copy("django", tmp_path)

    code, out = init([str(dir), "--backend", "warehouse=WAREHOUSE_URL"], Path("/"))

    assert code == 0, out
    harness = (dir / "tests/internal/harness.py").read_text()
    assert "import specrig.django\nfrom specrig import Backend, Postgres\n" in harness
    assert '    settings = "stock.settings"\n' in harness
    assert '    database = Postgres("DATABASE_URL")\n' in harness
    assert '    constants = {"SECRET_KEY": "' in harness
    assert '"SECRET_KEY": ""' not in harness


def depending(dir: Path, *dependencies: str) -> None:
    pyproject = dir / "pyproject.toml"
    document = tomlkit.parse(pyproject.read_text())
    document["project"]["dependencies"] = list(dependencies)
    pyproject.write_text(tomlkit.dumps(document))


@pytest.mark.parametrize(
    ("drivers", "database", "extra"),
    [
        (["aiomysql", "pymysql"], 'MySQL("DATABASE_URL", scheme="mysql+aiomysql")', "mysql"),
        (["asyncmy"], 'MySQL("DATABASE_URL", scheme="mysql+asyncmy")', "mysql"),
        (["pymysql"], 'MySQL("DATABASE_URL", scheme="mysql+pymysql")', "mysql"),
        (["mysqlclient"], 'MySQL("DATABASE_URL")', "mysql"),
        (["aiosqlite"], 'SQLite("DATABASE_URL", scheme="sqlite+aiosqlite")', "sqlite"),
    ],
)
def test_a_fastapi_service_is_given_the_database_its_driver_names(
    tmp_path: Path, drivers: list[str], database: str, extra: str
) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / ".env.example").unlink()
    depending(dir, "fastapi", "httpx", "pydantic-settings", "redis", "requests", "sqlalchemy[asyncio]", *drivers)

    code, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert code == 0, out
    harness = (dir / "tests/internal/harness.py").read_text()
    assert f"    database = {database}\n" in harness
    assert f"from specrig import {', '.join(sorted(['Backend', database.split('(')[0], 'Redis']))}\n" in harness
    assert tomlkit.parse((dir / "pyproject.toml").read_text())["dependency-groups"]["dev"] == [
        f"specrig[fastapi,{extra}]>=0.1"
    ]


def test_the_scheme_the_example_settings_name_wins_over_the_drivers(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / ".env.example").write_text("DATABASE_URL=mysql+pymysql://gateway@localhost/gateway\n")
    depending(dir, "fastapi", "httpx", "pydantic-settings", "redis", "requests", "sqlalchemy", "aiomysql", "pymysql")

    init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert (
        '    database = MySQL("DATABASE_URL", scheme="mysql+pymysql")\n'
        in (dir / "tests/internal/harness.py").read_text()
    )


def test_a_sqlite_address_in_the_example_settings_gives_a_service_with_no_driver_sqlite(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / ".env.example").write_text("DATABASE_URL=sqlite:///./gateway.db\n")
    depending(dir, "fastapi", "httpx", "pydantic-settings", "redis", "requests", "sqlalchemy")

    init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert '    database = SQLite("DATABASE_URL")\n' in (dir / "tests/internal/harness.py").read_text()


def test_a_service_with_no_driver_and_no_sqlite_address_is_given_no_database(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / ".env.example").unlink()
    depending(dir, "fastapi", "httpx", "pydantic-settings", "redis", "requests", "sqlalchemy")

    init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    harness = (dir / "tests/internal/harness.py").read_text()
    assert "database =" not in harness
    assert '"DATABASE_URL": ""' in harness
    assert tomlkit.parse((dir / "pyproject.toml").read_text())["dependency-groups"]["dev"] == ["specrig[fastapi]>=0.1"]


def test_a_sqlite_address_in_the_example_settings_leaves_a_postgres_service_on_postgres(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / ".env.example").write_text("DATABASE_URL=sqlite:///./dev.db\n")
    depending(dir, "fastapi", "httpx", "pydantic-settings", "redis", "requests", "sqlalchemy", "psycopg")

    init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert (
        '    database = Postgres("DATABASE_URL", scheme="postgresql+psycopg")\n'
        in (dir / "tests/internal/harness.py").read_text()
    )


@pytest.mark.parametrize(
    ("drivers", "engine", "database", "extra"),
    [
        (["mysqlclient"], None, 'MySQL("DATABASE_URL")', "mysql"),
        ([], "django.db.backends.sqlite3", 'SQLite("DATABASE_URL")', "sqlite"),
        (["psycopg"], "django.db.backends.sqlite3", 'SQLite("DATABASE_URL")', "sqlite"),
        ([], "django.db.backends.mysql", 'MySQL("DATABASE_URL")', "mysql"),
        ([], None, None, None),
    ],
)
def test_a_django_service_is_given_the_database_its_engine_or_its_driver_names(
    tmp_path: Path, drivers: list[str], engine: str | None, database: str | None, extra: str | None
) -> None:
    dir = copy("django", tmp_path)
    depending(dir, "django", "django-environ", "requests", *drivers)
    if engine is not None:
        settings = dir / "stock/settings.py"
        settings.write_text(
            settings.read_text().replace(
                'env.db("DATABASE_URL")', f'{{**env.db("DATABASE_URL"), "ENGINE": "{engine}"}}'
            )
        )

    init([str(dir), "--backend", "warehouse=WAREHOUSE_URL"], Path("/"))

    harness = (dir / "tests/internal/harness.py").read_text()
    assert (f"    database = {database}\n" in harness) if database else ("database =" not in harness)
    assert tomlkit.parse((dir / "pyproject.toml").read_text())["dependency-groups"]["dev"] == [
        f"specrig[django{f',{extra}' if extra else ''}]>=0.1"
    ]


def test_init_writes_an_agents_md_of_the_services_own_that_brings_specrig_md_in(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)

    _, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    stub = (dir / "AGENTS.md").read_text()
    assert stub.startswith(f"# AGENTS.md\n\nInstructions for AI coding agents working in gateway.\n\n{REFERENCE}\n")
    assert "\n## This service\n" in stub
    assert agents.verdict(stub) == "ok"
    assert "+ AGENTS.md\n+ specrig.md" in out


def test_init_adds_the_line_that_brings_specrig_md_in_to_the_end_of_an_agents_md_that_lacks_it(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / "AGENTS.md").write_text("ours")

    _, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert (dir / "AGENTS.md").read_text() == f"ours\n\n{REFERENCE}\n"
    assert "~ AGENTS.md: the line that brings specrig.md in, at its end" in out


def test_init_says_what_is_left_of_an_agents_md_that_still_carries_rule_11(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / "AGENTS.md").write_text("# AGENTS.md\n\n## Rule #11 — specification before code\n")

    _, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert (
        "it still carries Rule #11, which specrig.md holds now: keep the service's own sections in it and delete the rest"
        in out
    )
    assert cli.agents_md([str(dir)], Path("/")) == (
        1,
        "= specrig.md\nError: AGENTS.md still carries Rule #11, which specrig.md holds now: keep the service's own "
        "sections in it and delete the rest",
    )


def test_specrig_md_holds_nothing_init_was_told_so_agents_writes_it_as_init_did(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)

    init(
        [str(dir), "--backend", "crm=GATEWAY_CRM_URL", "--contract", "crm=../docs/crm.yaml", "--service", "kiosk"],
        Path("/"),
    )

    written = (dir / "specrig.md").read_text()
    assert "crm" not in written
    assert "kiosk" not in written
    assert "`make " not in written
    assert written.startswith("# specrig.md\n\n")
    assert "`.venv/bin/specrig agents`" in written
    assert "`.venv/bin/pytest tests/internal`" in written
    assert cli.agents_md([str(dir)], Path("/")) == (0, "= specrig.md")
    assert (dir / "specrig.md").read_text() == written


def test_agents_fails_while_agents_md_does_not_bring_specrig_md_in_and_writes_specrig_md_all_the_same(
    tmp_path: Path,
) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / "AGENTS.md").write_text("ours\n")

    code, out = cli.agents_md([str(dir)], Path("/"))

    assert code == 1
    assert out.startswith(
        f"+ specrig.md\nError: AGENTS.md does not bring specrig.md in: add the line\n\n{REFERENCE}\n\n"
    )
    assert (dir / "specrig.md").is_file()


def test_agents_writes_nothing_where_there_is_no_agents_md(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)

    with pytest.raises(ValueError, match="there is no AGENTS.md here"):
        cli.agents_md([str(dir)], Path("/"))
    assert not (dir / "specrig.md").exists()


def test_init_writes_no_file_that_exists(tmp_path: Path) -> None:
    dir = copy("fastapi", tmp_path)
    (dir / "tests/internal").mkdir(parents=True)
    (dir / "tests/internal/harness.py").write_text("mine")

    _, out = init([str(dir), "--backend", "crm=GATEWAY_CRM_URL"], Path("/"))

    assert "= tests/internal/harness.py — kept" in out
    assert (dir / "tests/internal/harness.py").read_text() == "mine"


def test_init_refuses_a_setting_the_code_does_not_read(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match="gateway reads no setting CRM_ADDRESS; it reads CRM_TOKEN, DATABASE_URL, GATEWAY_CRM_TIMEOUT, GATEWAY_CRM_URL, REDIS_URL",
    ):
        init([str(copy("fastapi", tmp_path)), "--backend", "crm=CRM_ADDRESS"], Path("/"))


def test_init_refuses_a_service_whose_neighbour_goes_unnamed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"name the neighbour with --backend <name>\[=<setting>\]"):
        init([str(copy("fastapi", tmp_path))], Path("/"))


def test_a_contract_for_no_backend_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="billing is not a backend; name it with --backend first"):
        init(
            [str(copy("fastapi", tmp_path)), "--backend", "crm", "--contract", "billing=../docs/billing.yaml"],
            Path("/"),
        )
