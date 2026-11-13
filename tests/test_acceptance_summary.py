from tools.summarize_acceptance import node_verdict


def test_passed_call_with_failed_teardown_is_failed():
    assert node_verdict([{'phase':'call','outcome':'passed'},
                         {'phase':'teardown','outcome':'failed'}])=='failed'


def test_unfinished_or_skipped_is_not_passed():
    assert node_verdict([{'phase':'setup','outcome':'passed'}])=='not_completed'
    assert node_verdict([{'phase':'setup','outcome':'skipped'}])=='skipped'
    assert node_verdict([{'phase':'call','outcome':'passed'}])=='not_completed'
