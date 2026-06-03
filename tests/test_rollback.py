import pathlib

from transaction_manager import TransactionManager


def test_transaction_manager_rollback_rename_chain(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    c = tmp_path / "c.txt"
    d = tmp_path / "d.txt"

    a.write_text("x")
    assert a.exists()

    tm = TransactionManager(root_dir=str(tmp_path))
    tm.begin_transaction()

    # A->B
    tm.record_action(action_type="rename", source_path=str(a), target_path=str(b))
    a.rename(b)

    # B->C
    tm.record_action(action_type="rename", source_path=str(b), target_path=str(c))
    b.rename(c)

    # C->D (fallará antes de ejecutar)
    tm.record_action(action_type="rename", source_path=str(c), target_path=str(d))

    # rollback debe revertir C->B y B->A, dejando A presente.
    tm.rollback()

    assert a.exists()
    assert not b.exists() or b.exists() is False
    assert not c.exists()
    assert not d.exists()

