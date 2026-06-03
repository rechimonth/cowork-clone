import sqlite3
from storage_manager import StorageManager


def test_sqlite_inserts_plan_and_actions(tmp_path):
    db_path = tmp_path / "cowork.db"
    sm = StorageManager(db_path=str(db_path))

    plan_id = "plan_1"
    execution_id = "exec_1"

    sm.insert_plan(plan_id=plan_id, status="created")
    sm.insert_action(plan_id=plan_id, execution_id=execution_id, action_type="rename", source_path="A", target_path="B", status="queued")
    sm.insert_execution(execution_id=execution_id, plan_id=plan_id, status="started")

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM plans")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM actions")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM executions")
        assert cur.fetchone()[0] == 1

