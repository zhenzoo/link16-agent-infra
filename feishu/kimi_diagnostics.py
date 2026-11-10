"""Evidence-limited Kimi request diagnostics. Never expose raw provider errors."""
from __future__ import annotations


def error_detail(record):
    """Accept only known error names/codes and numeric HTTP status, not messages."""
    error = record.get("error")
    error = error if isinstance(error, dict) else {}
    details = error.get("details")
    details = details if isinstance(details, dict) else {}
    name = record.get("errorName") or error.get("name")
    code = error.get("code")
    status = record.get("statusCode", details.get("statusCode"))
    status = status if type(status) is int and 400 <= status <= 599 else None
    if name == "APITimeoutError" or code == "provider.timeout":
        return "请求超时（APITimeoutError）：Kimi CLI 等待响应超过期限；网络、服务端或客户端的具体责任尚不能区分"
    if name == "APIConnectionError" or code == "provider.connection_error":
        return "连接失败（APIConnectionError）：Kimi CLI 未能完成与模型接口的通信；需核对网络、代理及接口可达性"
    if status in {401, 403} or code == "provider.auth_error":
        http = f"HTTP {status}" if status else "provider.auth_error"
        return f"认证或权限被接口拒绝（{http}）：需核对当前 Kimi 账号登录及模型权限；重建会话不会修复认证"
    if status == 429 or name == "APIProviderRateLimitError" or code == "provider.rate_limit":
        return "模型接口限流（HTTP 429）：服务端拒绝了这次请求；并发、速率或额度中的具体限制未由安全字段说明"
    if name == "APIProviderOverloadedError" or code == "provider.overloaded":
        return "模型接口报告过载（APIProviderOverloadedError）：上游暂时无法处理请求"
    if status and status >= 500:
        return f"模型接口／网关返回 HTTP {status}：上游处理失败；不能据此判定模型本身故障"
    if status:
        return f"模型接口拒绝请求（HTTP {status}）：需核对请求与服务限制；具体原因未由安全字段说明"
    if name == "APIStatusError":
        return "模型接口返回错误（APIStatusError），但原生记录没有可用 HTTP 状态码"
    return "原生记录没有可安全确认的错误类别或 HTTP 状态码；不能判定是网络、Kimi 客户端还是服务端"


class RequestDiagnostics:
    """Wire journals may buffer streaming content; silence is not proof of no bytes."""
    def __init__(self):
        self.request_offset = None
        self.started = None
        self.alerted = False
        self.last_error = None
        self.attempt = None
        self.max_attempts = None

    def consume(self, record, offset):
        kind = record.get("type")
        if kind == "llm.request" and record.get("kind", "loop") == "loop":
            self.request_offset = offset
            self.started = record.get("time", 0) / 1000
            return None
        if kind == "turn.step.retrying":
            self.started = None
            self.alerted = True
            self.last_error = error_detail(record)
            attempt, limit = record.get("nextAttempt"), record.get("maxAttempts")
            self.attempt = attempt if type(attempt) is int and attempt > 0 else None
            self.max_attempts = limit if type(limit) is int and limit > 0 else None
            detail = "Kimi CLI 已安排自动重试"
            if self.attempt:
                detail += f"，下一次为第 {self.attempt} 次"
                if self.max_attempts:
                    detail += f"／最多 {self.max_attempts} 次"
            return f"⚠️ Kimi {self.last_error}。{detail}；本轮尚未完成。"
        event = record.get("event") or {}
        response = kind == "usage.record" or (
            kind == "context.append_loop_event" and event.get("type") in {"content.part", "tool.call"}
        )
        if response:
            self.started = None
            self.last_error = None
            self.attempt, self.max_attempts = None, None
            if self.alerted:
                self.alerted = False
                return "🟢 Kimi 已产生新的模型响应记录，先前的请求等待／重试已恢复；本轮任务尚未完成，继续等待正常回复。"
        if kind == "turn.ended":
            self.started = None
        return None

    def waiting(self, now):
        if self.started is None or self.request_offset is None:
            return None
        elapsed = max(0, now - self.started)
        threshold = 300 if elapsed >= 300 else 60 if elapsed >= 60 else None
        if threshold is None:
            return None
        # Fixed thresholds make replay and restart labels deterministic.
        wait = "5 分钟" if threshold == 300 else "60 秒"
        label = (f"⏳ Kimi 已发起模型请求，至少 {wait} 没有新增模型响应记录。"
                 "消息已被原生会话接收；停在模型请求阶段，不是等待 new 或 Enter。"
                 "原生日志可能在流结束后才落盘，目前不能区分服务等待、网络停顿或客户端卡住。")
        if self.last_error:
            label += f" 最近一次已确认：{self.last_error}。"
        label += "桥仍在观察，未取消或重发任务；恢复或出现具体错误会另行提示。"
        if threshold == 300:
            label += "如需中断，可发 /stop 后在原会话继续，无需反复 /new。"
        return threshold, label

    def failed(self, record):
        detail = error_detail(record) if isinstance(record.get("error"), dict) else self.last_error
        if not detail:
            detail = "Kimi 原生执行已返回 failed，但没有具体错误字段；无法从这条记录进一步归责"
        return f"❌ Kimi 本轮未完成：{detail}。本轮已结束，CLI 不再自动重试；保留原会话，排除上述原因后再继续。"
