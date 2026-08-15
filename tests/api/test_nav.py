from src.api.routes._nav import home_path


def test_operator_home_is_the_workshop() -> None:
    assert home_path(True) == "/workshop"


def test_non_operator_home_is_the_parent_area() -> None:
    assert home_path(False) == "/parent"
