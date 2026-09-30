import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import solver  # noqa: E402


def test_basic_separation():
    groups = {"A": (0, 10), "B": (0, 10)}
    res = solver.solve(groups, [1, 2, 3, 4, 5, 6],
                       [("A", "B", 5)], {}, {})
    assert res is not None
    assert abs(res["A"] - res["B"]) >= 5


def test_non_overlapping_intervals_no_constraint():
    # 左闭右开：[0,10) 与 [10,20) 不相交，干扰关系不生效
    groups = {"A": (0, 10), "B": (10, 20)}
    res = solver.solve(groups, [5], [("A", "B", 100)], {}, {})
    assert res == {"A": 5, "B": 5}


def test_locks_respected():
    groups = {"A": (0, 10), "B": (0, 10)}
    res = solver.solve(groups, [1, 2, 3, 4, 5, 6, 7],
                       [("A", "B", 3)], {"A": 7}, {})
    assert res["A"] == 7
    assert abs(res["B"] - 7) >= 3


def test_infeasible_returns_none():
    # 3 组两两相交且间距 >=5，频点 1..9 无法容纳（需要跨度 >=10）
    groups = {"A": (0, 10), "B": (0, 10), "C": (0, 10)}
    rels = [("A", "B", 5), ("A", "C", 5), ("B", "C", 5)]
    assert solver.solve(groups, list(range(1, 10)), rels, {}, {}) is None


def test_lexicographic_tiebreak():
    groups = {"A": (0, 10), "B": (0, 10)}
    res = solver.solve(groups, [1, 2, 3, 4], [("A", "B", 3)], {}, {})
    # 字典序最小：A=1，B 取与 1 间距 >=3 的最小值 4
    assert res == {"A": 1, "B": 4}


def test_minimize_changes_keeps_previous():
    groups = {"A": (0, 10), "B": (0, 10)}
    prev = {"A": 4, "B": 1}
    res = solver.solve(groups, [1, 2, 3, 4], [("A", "B", 3)], {}, prev)
    assert res == {"A": 4, "B": 1}  # 旧方案仍可行，改频数 0


def test_minimize_changes_forced():
    groups = {"A": (0, 10), "B": (0, 10)}
    prev = {"A": 2, "B": 3}  # 旧方案本身不满足当前约束
    res = solver.solve(groups, [1, 2, 3, 4], [("A", "B", 3)], {}, prev)
    # 可行解只有 (1,4)/(4,1)，改频数均为 2，取字典序最小
    assert res == {"A": 1, "B": 4}


def test_first_plan_changes_zero_common_groups():
    groups = {"A": (0, 10), "B": (0, 10)}
    res = solver.solve(groups, [5, 7], [], {}, {})
    assert res == {"A": 5, "B": 5}


def test_explain_identifies_core():
    groups = {"A": (0, 10), "B": (0, 10), "C": (0, 10)}
    rels = [("A", "B", 5), ("A", "C", 5), ("B", "C", 5)]
    core, locked = solver.explain(groups, list(range(1, 10)), rels, {})
    assert len(core) == 3  # 三条关系缺一不可
    assert locked == []


def test_explain_with_locks():
    groups = {"A": (0, 10), "B": (0, 10)}
    rels = [("A", "B", 5)]
    core, locked = solver.explain(groups, [1, 2, 3, 4, 5], rels, {"A": 1})
    assert core == [("A", "B", 5)]
    assert locked == ["A"]


def test_conflicting_locks_infeasible():
    # 两个锁定组时段相交且锁定频点间距不足 → 无解
    groups = {"A": (0, 10), "B": (0, 10)}
    rels = [("A", "B", 3)]
    assert solver.solve(groups, [1, 2, 3, 4], rels,
                        {"A": 1, "B": 2}, {}) is None
    core, locked = solver.explain(groups, [1, 2, 3, 4], rels,
                                  {"A": 1, "B": 2})
    assert core == [("A", "B", 3)]
    assert locked == ["A", "B"]


def test_duplicate_relations_take_strictest():
    groups = {"A": (0, 10), "B": (0, 10)}
    rels = [("A", "B", 2), ("A", "B", 3)]
    res = solver.solve(groups, [1, 2, 3, 4], rels, {}, {})
    assert abs(res["A"] - res["B"]) >= 3
