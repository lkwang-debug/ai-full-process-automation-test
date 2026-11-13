"""FmClient 各域操作 mixin（按服务/功能拆分，混入 FmClient）。

每个 mixin 只定义本域的高层方法，依赖 FmClient 核心提供的共享状态与会话机械：
  _ensure_front / _ensure_conf / _require_conf / _make_session / _request / _send
  及 local_user_id / room_id / token / room_srv_node_id 等登录态。
"""
