"""
사여?! - 알림 발송 (v6: 검증 쇼크 + 매수 상태 전환)
=====================================================
- 🟢🟢 강력 매수 (strong 신규 진입)
- 🚨 지금 매수! (RSI 임계값 상향 돌파)
- 🚨 검증된 공식 발표 쇼크 (별도 07:00 KST 실행)
"""
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import hashlib

from event_alerts import due_shock_alerts
from notification_policy import build_buy_notification, evaluate_buy_alert

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()


# ============================================================
# 유틸
# ============================================================
def _mode_emoji(mode):
    return {
        "normal": "🟢 일반",
        "adjust": "🟡 조정",
        "caution": "🟠 경계",
        "panic": "🔴 공포",
    }.get(mode, mode)


def send_push(title, body, tag=None, data=None):
    tokens_ref = db.collection("fcm_tokens").stream()
    tokens = []
    for doc in tokens_ref:
        token_record = doc.to_dict()
        # 미승인 또는 알림 OFF는 제외
        if token_record.get("approved") is not True:
            continue
        if token_record.get("notifications_enabled") is False:
            continue
        tokens.append(doc.id)
    
    if not tokens:
        print(f"   ⚠️  토큰 없음: {title}")
        return 0
    
    sent = 0
    failed = 0
    for token in tokens:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                token=token,
                data=data or {},
                webpush=messaging.WebpushConfig(
                    notification=messaging.WebpushNotification(
                        title=title,
                        body=body,
                        icon="/icon-192.png",
                        badge="/icon-72.png",
                        tag=tag,
                        renotify=False,
                    ),
                    fcm_options=messaging.WebpushFCMOptions(
                        link="https://stock-sayo.web.app"
                    ),
                ),
                android=messaging.AndroidConfig(
                    notification=messaging.AndroidNotification(tag=tag) if tag else None
                ),
                apns=messaging.APNSConfig(
                    headers={"apns-collapse-id": tag} if tag else None,
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            thread_id=tag or "default",
                            sound="default",
                        )
                    )
                ),
            )
            messaging.send(message)
            sent += 1
        except Exception as e:
            failed += 1
            # 만료 토큰 정리
            if "Requested entity was not found" in str(e) or "registration-token-not-registered" in str(e):
                try:
                    db.collection("fcm_tokens").document(token).delete()
                except:
                    pass
    
    print(f"   📨 {title}: 발송 {sent} / 실패 {failed}")
    return sent


def send_due_event_shock_alerts(event_feed=None, now=None):
    """검증된 쇼크 결과를 다음 날 07:00 KST에 최대 한 번 발송한다."""
    if event_feed is None:
        try:
            doc = db.collection("stocks").document("data").get()
            event_feed = doc.to_dict().get("event_calendar") if doc.exists else None
        except Exception:
            print("   ⚠️  이벤트 feed 로드 실패")
            return 0

    sent_count = 0
    for alert in due_shock_alerts(event_feed, now=now):
        state_id = "event_shock_" + hashlib.sha256(
            alert["event_id"].encode("utf-8")
        ).hexdigest()[:32]
        state_ref = db.collection("state").document(state_id)
        try:
            claimed = _claim_event_shock(
                db.transaction(),
                state_ref,
                alert["event_id"],
                alert["notify_at"],
            )
        except Exception:
            print("   ⚠️  이벤트 쇼크 알림 claim 실패")
            continue
        if not claimed:
            continue

        try:
            delivered = send_push(
                alert["title"],
                alert["body"],
                tag=alert["tag"],
                data=alert["data"],
            )
        except Exception:
            delivered = 0
            print("   ⚠️  이벤트 쇼크 알림 발송 실패")

        if delivered <= 0:
            # 실제 전달 대상이 없거나 전송에 실패한 경우에만 재시도를 허용한다.
            # 성공 토큰이 하나라도 있으면 성공 토큰의 중복을 막기 위해 release하지 않는다.
            try:
                _release_event_shock_claim(db.transaction(), state_ref)
            except Exception:
                print("   ⚠️  이벤트 쇼크 알림 claim 해제 실패")
            continue

        try:
            state_ref.set({
                "sent": True,
                "status": "delivered",
                "event_id": alert["event_id"],
                "notify_at": alert["notify_at"],
                "delivered_at": firestore.SERVER_TIMESTAMP,
                "delivered_count": delivered,
            }, merge=True)
        except Exception:
            # claim은 그대로 둔다. 전달 뒤 상태 기록이 실패해도 재발송하지 않는
            # at-most-once 정책이 사용자 중복 방지 요구에 더 안전하다.
            print("   ⚠️  이벤트 쇼크 전달 상태 저장 실패 (claim 유지)")
        sent_count += 1

    if sent_count == 0:
        print("   📭 07:00 이벤트 쇼크 알림 없음")
    return sent_count


def send_due_event_result_alerts(event_feed, now=None):
    """하위 호환 호출점. 일반 발표 결과는 푸시하지 않는다.

    이벤트 피드는 화면에만 표시하고, 푸시는 ``send_due_event_shock_alerts``의
    객관적 쇼크 감사 기준을 통과한 경우에만 발송한다.
    """
    print("   📭 일반 발표 결과 푸시 비활성 (검증 쇼크만 발송)")
    return 0


# ============================================================
# 메인 알림 로직
# ============================================================
def check_and_notify(vix_data=None, qqq_data=None, kospi_data=None):
    """
    매크로 + 종목 시그널 검토 후 알림 발송.
    
    매수 푸시 대상:
    1. 🟢🟢 강력 매수 (신규 진입)
    2. 🚨 지금 매수! (RSI 임계값 상향 돌파)

    매수 후보와 위기·정보성·VIX 변화는 화면에만 표시한다. 푸시는 매수 상태
    전환과 별도 실행의 검증된 공식 발표 쇼크만 발송한다.
    """
    print(f"\n📊 시장 모드: 미국 {_mode_emoji((qqq_data or {}).get('above_ma20', True) and 'normal' or 'adjust')} | "
          f"한국 {_mode_emoji((kospi_data or {}).get('above_ma20', True) and 'normal' or 'adjust')} "
          f"(VIX: {(vix_data or {}).get('current', '?')})")
    
    # === Firestore 데이터 로드 ===
    try:
        doc = db.collection("stocks").document("data").get()
        if not doc.exists:
            print("   📭 stocks/data 문서 없음")
            return
        data = doc.to_dict()
    except Exception as e:
        print(f"   ⚠️  Firestore 로드 실패: {e}")
        return
    
    all_stocks = (
        [(stock, "stock") for stock in (data.get("kr_stock") or [])]
        + [(stock, "stock") for stock in (data.get("us_stock") or [])]
        + [(stock, "etf") for stock in (data.get("kr_etf") or [])]
    )
    sample_id = (
        data.get("collection_finished_at")
        or data.get("last_attempt_at")
        or data.get("updated")
    )
    
    # === 이전 RSI 상태 로드 (돌파 감지용) ===
    try:
        rsi_state_doc = db.collection("state").document("rsi").get()
        prev_rsi_map = rsi_state_doc.to_dict() if rsi_state_doc.exists else {}
    except:
        prev_rsi_map = {}
    
    new_rsi_map = {}
    sent_count = 0
    
    # === 종목별 시그널 처리 ===
    for stock, instrument_type in all_stocks:
        code = stock.get("code")
        if not code:
            continue
        
        rsi = stock.get("rsi")
        # 새 RSI 상태 저장용
        if rsi is not None:
            new_rsi_map[code] = rsi
        
        prev_rsi = prev_rsi_map.get(code)

        # ============================================================
        # 1~2. 강력 매수 신규 진입 / RSI 상향 돌파
        # ============================================================
        # 상태 확인과 이번 회차 claim을 transaction으로 묶는다. FCM 발송보다
        # 먼저 claim해 중복 방지를 우선하며, 상태 장애 시에는 발송하지 않는다.
        prev_state_doc_id = f"buy_zone_{code}"
        try:
            state_ref = db.collection("state").document(prev_state_doc_id)
            alert = _claim_buy_alert(
                db.transaction(),
                state_ref,
                stock,
                prev_rsi,
                sample_id,
            )
        except Exception:
            print(f"   ⚠️  매수 알림 상태 갱신 실패: {code}")
            alert = None

        if alert:
            notification = build_buy_notification(alert, stock, instrument_type)
            delivered = send_push(
                notification["title"],
                notification["body"],
                tag=notification["tag"],
                data=notification["data"],
            )
            if delivered > 0:
                sent_count += 1
            try:
                state_ref.set({
                    "last_delivery_status": "delivered" if delivered > 0 else "failed",
                    "last_delivery_type": alert["type"],
                    "last_delivery_sample_id": sample_id,
                    "last_delivery_at": firestore.SERVER_TIMESTAMP,
                }, merge=True)
            except Exception:
                print(f"   ⚠️  매수 알림 전달 상태 저장 실패: {code}")
        
    # === RSI 상태 저장 (다음 tick에서 돌파 감지용) ===
    if new_rsi_map:
        try:
            db.collection("state").document("rsi").set(new_rsi_map)
        except Exception as e:
            print(f"   ⚠️  RSI 상태 저장 실패: {e}")
    
    # === 결과 ===
    if sent_count == 0:
        print("   📭 새 시그널 없음")
    else:
        print(f"   ✅ 총 {sent_count}건 발송")


@firestore.transactional
def _claim_buy_alert(transaction, state_ref, stock, fallback_prev_rsi, sample_id):
    snapshot = state_ref.get(transaction=transaction)
    previous_state = snapshot.to_dict() if snapshot.exists else {}
    alert, next_state = evaluate_buy_alert(
        stock,
        previous_state=previous_state,
        fallback_prev_rsi=fallback_prev_rsi,
        sample_id=sample_id,
    )
    next_state["observed_at"] = firestore.SERVER_TIMESTAMP
    if alert:
        next_state["last_claimed_type"] = alert["type"]
        next_state["last_claimed_at"] = firestore.SERVER_TIMESTAMP
    transaction.set(state_ref, next_state, merge=True)
    return alert


@firestore.transactional
def _claim_event_shock(transaction, state_ref, event_id, notify_at):
    """동시 실행 중 하나만 쇼크 발송 권한을 갖도록 원자적으로 선점한다."""
    snapshot = state_ref.get(transaction=transaction)
    previous_state = snapshot.to_dict() if snapshot.exists else {}
    if (
        previous_state.get("sent") is True
        or previous_state.get("status") in {"claimed", "delivered"}
    ):
        return False

    transaction.set(state_ref, {
        "sent": False,
        "status": "claimed",
        "event_id": event_id,
        "notify_at": notify_at,
        "claimed_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return True


@firestore.transactional
def _release_event_shock_claim(transaction, state_ref):
    """전달 0건인 선점만 재시도 가능 상태로 돌린다."""
    snapshot = state_ref.get(transaction=transaction)
    state = snapshot.to_dict() if snapshot.exists else {}
    if state.get("sent") is True or state.get("status") != "claimed":
        return False
    transaction.set(state_ref, {
        "status": "retryable",
        "released_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return True
