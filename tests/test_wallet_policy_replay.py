from scripts.wallet_policy_replay import WalletPolicy


def test_one_loss_and_two_loss_locks_are_distinct():
    one, two = WalletPolicy(1, 0), WalletPolicy(2, 0)
    one.close("w", -0.1, 100); two.close("w", -0.1, 100)
    assert one.blocked("w", 101)
    assert not two.blocked("w", 101)
    two.close("w", -0.1, 200)
    assert two.blocked("w", 201)


def test_cooldown_expires_and_resets_after_a_new_loss():
    state = WalletPolicy(0, 300)
    state.close("w", -0.1, 100)
    assert state.blocked("w", 399)
    assert not state.blocked("w", 400)
    state.close("w", -0.1, 500)
    assert state.blocked("w", 799)
    assert not state.blocked("w", 800)


def test_winning_close_does_not_lock_wallet():
    state = WalletPolicy(1, 300)
    state.close("w", 0.1, 100)
    assert not state.blocked("w", 101)
