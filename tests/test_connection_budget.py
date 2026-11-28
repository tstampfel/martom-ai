"""The API's Postgres connection budget fits the RDS instance (#21 review r2)."""

from pathlib import Path

from martom import checkpointer, db
from martom.api import threads


def test_the_pools_are_sized_in_one_place() -> None:
    assert threads.LOCK_POOL_MAX_SIZE is db.LOCK_POOL_MAX_SIZE
    assert checkpointer.POOL_MAX_SIZE is db.CHECKPOINTER_POOL_MAX_SIZE


def test_every_running_turn_can_get_a_query_connection() -> None:
    # A turn holds one lock connection and borrows at most one query connection at a time.
    assert db.LOCK_POOL_MAX_SIZE <= db.QUERY_POOL_MAX_SIZE


def test_two_api_tasks_and_the_jobs_leave_headroom_on_the_instance() -> None:
    per_task = (
        db.QUERY_POOL_MAX_SIZE
        + db.LOCK_POOL_MAX_SIZE
        + db.CHECKPOINTER_POOL_MAX_SIZE
        + db.API_UNPOOLED_CONNECTIONS
    )
    total = db.API_TASKS_DURING_DEPLOY * per_task + db.JOB_CONNECTIONS + db.UMAMI_CONNECTIONS
    usable = db.RDS_MAX_CONNECTIONS - db.RDS_RESERVED_CONNECTIONS
    assert total <= 0.75 * usable  # at least a quarter left for psql, retries, spikes


def test_umami_role_limit_matches_the_budget() -> None:
    # infra/umami.tf creates the umami role with this CONNECTION LIMIT; the budget above
    # counts the same number.
    setup = (Path(__file__).parents[1] / "infra" / "umami.tf").read_text()
    assert f"CONNECTION LIMIT {db.UMAMI_CONNECTIONS} " in setup
