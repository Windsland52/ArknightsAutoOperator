"""calibration 纯逻辑测试（_jaccard / _cluster）。"""

from __future__ import annotations

from aao.core.timing import calibration


class TestJaccard:
    def test_identical(self) -> None:
        assert calibration._jaccard({1, 2, 3}, {1, 2, 3}) == 1.0

    def test_disjoint(self) -> None:
        assert calibration._jaccard({1, 2}, {3, 4}) == 0.0

    def test_partial(self) -> None:
        # {1,2} vs {1,3}: 交={1}=1, 并={1,2,3}=3 → 1/3
        assert abs(calibration._jaccard({1, 2}, {1, 3}) - 1 / 3) < 1e-9

    def test_empty_both(self) -> None:
        assert calibration._jaccard(set(), set()) == 1.0

    def test_one_empty(self) -> None:
        assert calibration._jaccard({1}, set()) == 0.0


class TestCluster:
    def test_similar_grouped(self) -> None:
        # 两个高度相似样本(Jaccard≥0.8) + 一个差异大 → 两簇
        similar_a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        similar_b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11]  # 与 a 仅差1个 → 9/11≈0.82
        different = [100, 200, 300]
        clusters = calibration._cluster([similar_a, similar_b, different])
        assert len(clusters) == 2
        # 相似的俩在同簇（大小2）
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [1, 2]

    def test_all_similar_one_cluster(self) -> None:
        s1 = [1, 2, 3]
        s2 = [1, 2, 3]
        clusters = calibration._cluster([s1, s2])
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_empty_sample_skipped(self) -> None:
        # 空样本（sample_set 为空）跳过不建簇
        clusters = calibration._cluster([[], [1, 2]])
        assert len(clusters) == 1
