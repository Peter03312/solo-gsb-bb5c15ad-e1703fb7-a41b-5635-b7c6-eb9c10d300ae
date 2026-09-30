"""频点分配精确求解器（纯 Python，无外部依赖）。

目标优先级：
1. 满足全部硬约束（锁定频点 + 时段相交干扰组的最小频点间距）；
2. 最小化新旧版本共有组的改频数（首次规划时共有组为空，计零）；
3. 并列时按组编号升序比较频点序列，取字典序最小方案。

求解方法：前向检查 + 分支限界的深度优先搜索。
第一阶段按改频预算 0,1,2,... 递增试探，首个可行预算即为最少改频数；
第二阶段在该预算下按组编号升序逐组固定其可取的最小频点，
得到字典序最小方案。
"""
from __future__ import annotations


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """左闭右开时段 [start, end) 是否相交。"""
    return a_start < b_end and b_start < a_end


def _build_neighbors(groups: dict, interferences: list) -> dict:
    """仅保留时段相交的干扰关系；同一对组取最严格的间距。"""
    neighbors = {g: {} for g in groups}
    for a, b, sep in interferences:
        if overlaps(*groups[a], *groups[b]):
            if sep > neighbors[a].get(b, 0):
                neighbors[a][b] = sep
                neighbors[b][a] = sep
    return neighbors


def _restricted(domain: list, f: int, sep: int) -> list:
    return [v for v in domain if abs(v - f) >= sep]


def _feasible(groups, frequencies, neighbors, fixed, previous, budget):
    """在 fixed（锁定/已选定）基础上求一个可行分配；不可行返回 None。

    budget 为允许的最多改频数（仅统计 previous 中出现的组），None 表示不限。
    """
    # 已固定组（锁定/已选定）之间的相互冲突
    fixed_items = list(fixed.items())
    for i in range(len(fixed_items)):
        g1, f1 = fixed_items[i]
        for g2, f2 in fixed_items[i + 1:]:
            sep = neighbors[g1].get(g2)
            if sep and abs(f1 - f2) < sep:
                return None

    domains = {}
    for g in groups:
        if g in fixed:
            continue
        dom = list(frequencies)
        for n, sep in neighbors[g].items():
            if n in fixed:
                dom = _restricted(dom, fixed[n], sep)
        if not dom:
            return None
        domains[g] = dom

    def changes_lower_bound(domains, fixed):
        if budget is None:
            return 0
        c = 0
        for g, f in fixed.items():
            if g in previous and previous[g] != f:
                c += 1
        for g, dom in domains.items():
            if g in previous and previous[g] not in dom:
                c += 1  # 旧频点已不在值域中，必然改频
        return c

    def dfs(domains, fixed):
        if budget is not None and changes_lower_bound(domains, fixed) > budget:
            return None
        if not domains:
            return dict(fixed)
        # MRV：选值域最小的未分配组
        g = min(domains, key=lambda x: len(domains[x]))
        dom = domains.pop(g)
        for f in dom:
            new_domains = {}
            ok = True
            for h, hdom in domains.items():
                sep = neighbors[h].get(g)
                nd = _restricted(hdom, f, sep) if sep else hdom
                if not nd:
                    ok = False
                    break
                new_domains[h] = nd
            if not ok:
                continue
            fixed[g] = f
            res = dfs(new_domains, fixed)
            if res is not None:
                return res
            del fixed[g]
        domains[g] = dom
        return None

    return dfs(domains, fixed)


def solve(groups: dict, frequencies: list, interferences: list,
          locks: dict, previous: dict):
    """求最优分配，返回 {组编号: 频点}；无解返回 None。

    groups: {组编号: (start, end)}，时段左闭右开
    frequencies: 可用整数频点列表
    interferences: [(组a, 组b, 最小间距)]，均为正整数间距
    locks: {组编号: 锁定频点}
    previous: 上一发布版的 {组编号: 频点}（可含已删除的组，自动忽略）
    """
    frequencies = sorted(set(frequencies))
    neighbors = _build_neighbors(groups, interferences)
    previous = {g: f for g, f in (previous or {}).items() if g in groups}
    locks = dict(locks or {})

    # 第一阶段：递增改频预算，找到最少改频数
    common = [g for g in groups if g in previous]
    best = None
    for budget in range(0, len(common) + 1):
        best = _feasible(groups, frequencies, neighbors, dict(locks),
                         previous, budget)
        if best is not None:
            break
    if best is None:
        return None
    min_changes = sum(1 for g in common if best[g] != previous[g])

    # 第二阶段：在最少改频预算下，按组编号升序逐组取最小可行频点
    fixed = dict(locks)
    for g in sorted(groups):
        if g in fixed:
            continue
        cand = list(frequencies)
        for n, sep in neighbors[g].items():
            if n in fixed:
                cand = _restricted(cand, fixed[n], sep)
        for f in sorted(cand):
            trial = dict(fixed)
            trial[g] = f
            if _feasible(groups, frequencies, neighbors, trial,
                         previous, min_changes) is not None:
                fixed[g] = f
                break
        else:  # 理论上不可达：第一阶段已保证可行
            return None
    return fixed


def explain(groups: dict, frequencies: list, interferences: list,
            locks: dict):
    """无解时给出一组（近似最小的）致冲突干扰关系及涉及的锁定组。

    贪心删除：若去掉某条干扰关系后仍无解，则该关系不是冲突的必要条件，
    将其移除；最终剩下的关系集与锁定共同导致无解。
    """
    frequencies = sorted(set(frequencies))
    locks = dict(locks or {})
    core = list(interferences)
    i = 0
    while i < len(core):
        trial = core[:i] + core[i + 1:]
        neighbors = _build_neighbors(groups, trial)
        if _feasible(groups, frequencies, neighbors, dict(locks), {}, None) is None:
            core = trial
        else:
            i += 1
    involved_locks = sorted({g for a, b, _ in core for g in (a, b)
                             if g in locks})
    return core, involved_locks
