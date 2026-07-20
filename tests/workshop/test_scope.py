from src.workshop.scope import OPERATOR_STORE_TOKEN, WorkshopScope, resolve_scope


def test_operator_role_gets_global_operator_scope():
    scope = resolve_scope({"sub": "user_op", "role": "operator", "family_token": "ignored"})
    assert scope == WorkshopScope(
        user_id="user_op",
        is_operator=True,
        store_token=OPERATOR_STORE_TOKEN,
        publish_target="shared",
    )


def test_non_operator_with_family_token_gets_family_scope():
    scope = resolve_scope({"sub": "user_p", "family_token": "fam_42"})
    assert scope == WorkshopScope(
        user_id="user_p",
        is_operator=False,
        store_token="fam_42",
        publish_target="overlay",
    )


def test_non_operator_without_family_token_is_a_parent_with_empty_store_token():
    scope = resolve_scope({"sub": "user_new"})
    assert scope.is_operator is False
    assert scope.store_token == ""
    assert scope.publish_target == "overlay"


def test_any_role_other_than_operator_is_a_parent():
    scope = resolve_scope({"sub": "u", "role": "admin", "family_token": "fam_1"})
    assert scope.is_operator is False
    assert scope.store_token == "fam_1"
