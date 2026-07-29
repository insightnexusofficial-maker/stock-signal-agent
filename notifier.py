"""
사여?! - 알림 발송 (v5: 강력 매수 + 지금 매수 + 위기 트리거)
==========================================================
- 🟢🟢 강력 매수 (strong 신규 진입)
- 🚨 지금 매수! (RSI 임계값 상향 돌파)
- 🚨 기업 위기 / 🌪️ 시장 위기 (3대 트리거)
- 🟡 EPS 추세 정보성 (참고용)
- ⚡ VIX 반전 (특별 기회)
"""
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import hashlib

from event_alerts import due_result_alerts, due_shock_alerts
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
    """검증된 쇼크 결과를 다음 날 07:00 KST에 한 번만 발송한다."""
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
        try:
            state_doc = db.collection("state").document(state_id).get()
            already_sent = state_doc.exists and state_doc.to_dict().get("sent") is True
        except Exception:
            print("   ⚠️  이벤트 알림 중복 방지 상태 확인 실패")
            continue
        if already_sent:
            continue
        delivered = send_push(
            alert["title"],
            alert["body"],
            tag=alert["tag"],
            data=alert["data"],
        )
        if delivered <= 0:
            continue
        try:
            db.collection("state").document(state_id).set({
                "sent": True,
                "event_id": alert["event_id"],
                "notify_at": alert["notify_at"],
            })
        except Exception:
            print("   ⚠️  이벤트 알림 중복 방지 상태 저장 실패")
            continue
        sent_count += 1

    if sent_count == 0:
        print("   📭 07:00 이벤트 쇼크 알림 없음")
    return sent_count


def send_due_event_result_alerts(event_feed, now=None):
    """공식 확인이 끝난 주요 발표 결과를 검증 시각 기준으로 한 번 발송한다."""
    sent_count = 0
    for alert in due_result_alerts(event_feed, now=now):
        state_id = "event_result_" + hashlib.sha256(
            alert["event_id"].encode("utf-8")
        ).hexdigest()[:32]
        try:
            state_doc = db.collection("state").document(state_id).get()
            already_sent = state_doc.exists and state_doc.to_dict().get("sent") is True
        except Exception:
            print("   ⚠️  발표 결과 알림 중복 방지 상태 확인 실패")
            continue
        if already_sent:
            continue
        delivered = send_push(
            alert["title"],
            alert["body"],
            tag=alert["tag"],
            data=alert["data"],
        )
        if delivered <= 0:
            continue
        try:
            db.collection("state").document(state_id).set({
                "sent": True,
                "event_id": alert["event_id"],
                "retrieved_at": alert["retrieved_at"],
            })
        except Exception:
            print("   ⚠️  발표 결과 알림 중복 방지 상태 저장 실패")
            continue
        sent_count += 1

    if sent_count == 0:
        print("   📭 새 공식 발표 결과 알림 없음")
    return sent_count


# ============================================================
# 메인 알림 로직
# ============================================================
def check_and_notify(vix_data=None, qqq_data=None, kospi_data=None):
    """
    매크로 + 종목 시그널 검토 후 알림 발송.
    
    매수 푸시 대상:
    1. 🟢🟢 강력 매수 (신규 진입)
    2. 🚨 지금 매수! (RSI 임계값 상향 돌파)

    매수 후보는 화면에만 표시한다. 기존 위기·정보성·이벤트 알림은 유지한다.
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
        name = stock.get("name", "?")
        if not code:
            continue
        
        rsi = stock.get("rsi")
        rsi_threshold = stock.get("rsi_threshold")
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
        
        # ============================================================
        # 3. 🚨 기업 위기 / 🌪️ 시장 위기
        # ============================================================
        triggers = stock.get("crisis_triggers") or []
        details = stock.get("crisis_details") or []
        
        for i, trigger in enumerate(triggers):
            detail = details[i] if i < len(details) else ""
            
            # 중복 발송 방지
            crisis_state_id = f"crisis_{trigger}_{code}"
            try:
                prev_crisis = db.collection("state").document(crisis_state_id).get()
                already_sent = prev_crisis.exists and prev_crisis.to_dict().get("sent", False)
            except:
                already_sent = False
            
            if already_sent:
                continue
            
            if trigger == "company_crisis":
                title = f"🚨 기업 위기: {name}"
                body = detail if detail else "EPS+목표주가 동반 하락 + 매출/서프 쇼크"
            elif trigger == "market_panic":
                title = f"🌪️ 시장 위기 진입"
                body = detail if detail else "VIX 40+ 및 지수 MA 하향. 현금화 검토."
            else:
                continue
            
            send_push(title, body, tag=f"crisis-{trigger}-{code}", data={
                "type": "crisis",
                "trigger": trigger,
                "code": code,
            })
            sent_count += 1
            
            try:
                db.collection("state").document(crisis_state_id).set({"sent": True})
            except:
                pass
        
        # ============================================================
        # 4. 🟡 EPS 추세 정보성 (참고용, 매도 권고 아님)
        # ============================================================
        info_level = stock.get("info_level")
        info_reasons = stock.get("info_reasons") or []
        
        if info_level:
            # 같은 레벨 중복 발송 방지 (하루 1번)
            info_state_id = f"info_{code}"
            try:
                prev_info_doc = db.collection("state").document(info_state_id).get()
                prev_info_level = prev_info_doc.to_dict().get("level") if prev_info_doc.exists else None
            except:
                prev_info_level = None
            
            if prev_info_level != info_level:
                if info_level == "info_watch":
                    emoji_label = "🟠 관찰"
                elif info_level == "info_warn":
                    emoji_label = "🟡 주의"
                else:
                    emoji_label = "🔵 정보"
                
                title = f"{emoji_label}: {name}"
                body = " | ".join(info_reasons) if info_reasons else "EPS 추세 변화"
                send_push(title, body, tag=f"info-{code}", data={
                    "type": "info",
                    "level": info_level,
                    "code": code,
                })
                sent_count += 1
                
                try:
                    db.collection("state").document(info_state_id).set({"level": info_level})
                except:
                    pass
    
    # === RSI 상태 저장 (다음 tick에서 돌파 감지용) ===
    if new_rsi_map:
        try:
            db.collection("state").document("rsi").set(new_rsi_map)
        except Exception as e:
            print(f"   ⚠️  RSI 상태 저장 실패: {e}")
    
    # ============================================================
    # 5. ⚡ VIX 반전 (공포 → 평온 전환)
    # ============================================================
    if vix_data and vix_data.get("reversal"):
        try:
            vix_state = db.collection("state").document("vix_reversal").get()
            already = vix_state.exists and vix_state.to_dict().get("sent_today", False)
        except:
            already = False
        
        if not already:
            current = vix_data.get("current", "?")
            send_push(
                "⚡ VIX 하락 반전",
                f"VIX {current} 꺾임. 공포 완화 시작 — 분할 매수 검토.",
                tag="vix-reversal",
                data={"type": "vix_reversal"},
            )
            sent_count += 1
            try:
                db.collection("state").document("vix_reversal").set({"sent_today": True})
            except:
                pass
    
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
