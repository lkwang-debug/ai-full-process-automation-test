import pytest
from meeting_test_runtime.group_assertions import member_pairs, member_states


def test_group_members_support_repeated_nodes_and_parallel_fields():
    expected={'1':'2','3':'1'}
    assert member_pairs([{'ID':1,'Right':2},{'ID':3,'Right':1}])==expected
    assert member_pairs({'ID':[1,3],'Right':[2,1]})==expected
    with pytest.raises(AssertionError):member_pairs({'ID':[1,3],'Right':2})


def test_group_member_state_is_present_and_valid():
    assert member_states([{'ID': 1, 'State': 0}, {'ID': 2, 'State': 2}]) == {
        '1': '0', '2': '2'}
    with pytest.raises(AssertionError):
        member_states({'ID': [1, 2], 'State': 0})
    with pytest.raises(AssertionError):
        member_states({'ID': 1, 'State': 9})
