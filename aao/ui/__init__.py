"""UI 包：主控台 + 各功能页 + 后台 worker。

单进程 Option B：一个 QMainWindow 主控台（侧栏导航 + QStackedWidget 内容区）
+ 独立悬浮窗，共享同一个 controller / MeasurementWorker。
"""
