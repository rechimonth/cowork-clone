import pathlib

from transaction_manager import TransactionManager


def test_transaction_manager_records_and_rollbacks_mkdir(tmp_path):
    tm = TransactionManager(root_dir=str(tmp_path))

    # Creamos un directorio "registrado" y luego lo revertimos vía TransactionManager.
    # Nota: rollback solo puede revertir lo que fue registrado.
    created = tmp_path / "dest"
    assert not created.exists()

    tm.begin_transaction()
    tm.record_action(action_type="mkdir", source_path=None, target_path=str(created))
    # Simulamos que la creación ocurrió
    created.mkdir(parents=True, exist_ok=False)

    tm.rollback()
    assert not created.exists()

