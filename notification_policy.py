"""매수 푸시 정책을 Firebase 입출력과 분리한 순수 로직."""

from datetime import datetime, time, timedelta, timezone


BUY_ALERT_STATE_VERSION = 2
DEFAULT_RSI_ZONE_OFFSET = 10
KST = timezone(timedelta(hours=9))
KR_MARKET_OPEN = time(9, 0)
KR_MARKET_CLOSE = time(15, 30)


def is_kr_buy_alert_session(now=None):
    """한국 주식·ETF 매수 푸시를 평가해도 되는 정규장 시간인지 확인한다."""
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    return (
        now.weekday() < 5
        and KR_MARKET_OPEN <= now.time().replace(tzinfo=None) < KR_MARKET_CLOSE
    )


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_number(value, decimals=1):
    number = _number(value)
    if number is None:
        return "—"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.{decimals}f}"


def _is_same_or_older_sample(incoming, stored):
    if not incoming or not stored:
        return False
    if incoming == stored:
        return True
    try:
        incoming_at = datetime.fromisoformat(str(incoming))
        stored_at = datetime.fromisoformat(str(stored))
    except (TypeError, ValueError):
        return False
    if incoming_at.tzinfo is None or stored_at.tzinfo is None:
        return False
    return incoming_at <= stored_at


def _etf_reasons(stock):
    details = set(stock.get("selection_hit_details") or [])
    reasons = []

    rsi = _number(stock.get("rsi"))
    threshold = _number(stock.get("rsi_threshold"))
    if rsi is not None and threshold is not None:
        reasons.append(f"RSI {_display_number(rsi)} (과매도 기준 {threshold:g})")

    nav_discount = _number(stock.get("nav_discount"))
    if "nav_discount" in details and nav_discount is not None:
        if nav_discount < 0:
            reasons.append(f"NAV 대비 {abs(nav_discount):.2f}% 할인")
        else:
            reasons.append(f"NAV 대비 {nav_discount:.2f}% 프리미엄")

    band_pct = _number(stock.get("band_pct"))
    if "band_position" in details and band_pct is not None:
        reasons.append(f"52주 위치 {band_pct:.1f}%")

    return reasons


def evaluate_buy_alert(stock, previous_state=None, fallback_prev_rsi=None, sample_id=None):
    """한 종목의 이번 매수 알림과 다음 관측 상태를 계산한다.

    매수 후보는 화면 상태로만 유지한다. 푸시는 강력 매수 진입과 RSI 상향
    돌파(지금 매수)만 만든다. 동일 회차에 둘이 겹치면 지금 매수를 우선한다.
    """
    previous_state = previous_state or {}
    previous_rsi = _number(previous_state.get("prev_rsi"))
    if previous_rsi is None:
        previous_rsi = _number(fallback_prev_rsi)

    previous_strong = previous_state.get("strong_active")
    if previous_strong is None:
        previous_strong = bool(previous_state.get("in_zone", False))
    previous_strong = bool(previous_strong)

    current_rsi = _number(stock.get("rsi"))
    threshold = _number(stock.get("rsi_threshold"))
    zone_upper = _number(stock.get("rsi_zone_upper"))
    if zone_upper is None and threshold is not None:
        zone_upper = threshold + DEFAULT_RSI_ZONE_OFFSET

    initialized = previous_state.get("schema_version") == BUY_ALERT_STATE_VERSION
    same_or_older_sample = _is_same_or_older_sample(
        sample_id,
        previous_state.get("sample_id"),
    )
    stale = bool(stock.get("is_stale"))
    step1 = bool(stock.get("step1"))
    strong_active = step1 and stock.get("buy_level") == "strong"
    in_zone = bool(stock.get("in_buy_zone", False))

    if same_or_older_sample:
        return None, dict(previous_state)

    next_state = {
        "schema_version": BUY_ALERT_STATE_VERSION,
        "sample_id": sample_id,
        "prev_rsi": current_rsi,
        "in_zone": in_zone,
        "strong_active": strong_active,
    }

    # 직전값으로 채운 회차는 신호 전이를 만들지 않는다. 마지막 신선 관측값을
    # 보존해 다음 정상 회차가 실제 전이를 판단하게 한다.
    if stale:
        next_state["prev_rsi"] = previous_rsi
        next_state["in_zone"] = bool(previous_state.get("in_zone", False))
        next_state["strong_active"] = previous_strong
        return None, next_state

    crossed_up = (
        step1
        and previous_rsi is not None
        and current_rsi is not None
        and threshold is not None
        and zone_upper is not None
        and previous_rsi < threshold <= current_rsi <= zone_upper
    )

    alert_type = None
    if crossed_up:
        alert_type = "buy_now"
    elif initialized and strong_active and not previous_strong:
        alert_type = "strong_buy"

    if alert_type is None:
        return None, next_state
    return {
        "type": alert_type,
        "previous_rsi": previous_rsi,
    }, next_state


def build_buy_notification(alert, stock, instrument_type):
    """실제 판정 근거만 사용해 매수 푸시 제목과 본문을 만든다."""
    alert_type = alert["type"]
    name = stock.get("name", "?")
    code = stock.get("code", "")
    rsi = _number(stock.get("rsi"))
    threshold = _number(stock.get("rsi_threshold"))
    price = _display_number(stock.get("price"), decimals=2)
    is_etf = instrument_type == "etf"

    if alert_type == "buy_now":
        previous_rsi = _number(alert.get("previous_rsi"))
        emoji = "🚨💪" if stock.get("buy_level") == "strong" else "🚨"
        title = f"{emoji} 지금 매수! {name}"
        parts = [
            f"RSI {_display_number(previous_rsi)} → {_display_number(rsi)} "
            f"(기준 {threshold:g} 상향 돌파)"
        ]
        if is_etf:
            parts.extend(_etf_reasons(stock)[1:])
        elif stock.get("buy_level") == "strong":
            parts.append("강력 매수 조건")
        else:
            parts.append("매수 조건 통과")
        parts.append(price)
        return {
            "title": title,
            "body": " | ".join(parts),
            "tag": f"buy-now-{code}",
            "data": {
                "type": "buy_now",
                "code": code,
                "level": stock.get("buy_level", "none"),
            },
        }

    title = f"🟢🟢 강력 매수: {name}"
    if is_etf:
        parts = _etf_reasons(stock)
    else:
        parts = [
            f"RSI {_display_number(rsi)} "
            f"(구간 {threshold:g}~{threshold + DEFAULT_RSI_ZONE_OFFSET:g})",
            "EPS·목표 동반 개선",
        ]
    parts.append(price)
    return {
        "title": title,
        "body": " | ".join(parts),
        "tag": f"strong-buy-{code}",
        "data": {
            "type": "strong_buy",
            "code": code,
            "level": "strong",
        },
    }
