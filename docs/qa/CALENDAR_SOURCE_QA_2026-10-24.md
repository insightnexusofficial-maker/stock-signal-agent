# 공식 일정 수집 독립 실행·출처 QA

관측: 2026-10-24 22:40 KST, 로컬 curl로 공식 기업 일정 URL 23개를 각 1회 점검했다. 응답 본문·헤더는 출력하거나 파일에 저장하지 않았다. HTTP 성공 시에만 같은 날짜 발견 파서를 메모리에서 실행했다. 정형 결과는 [출처 상태 JSON](CALENDAR_SOURCE_STATUS_2026-10-24.json)에 보존했다.

## 직접 관측

| 대상 | HTTP | 날짜 파서 |
| --- | --- | --- |
| 005930 | 404 | 접근 실패로 미실행 |
| 000660 | 404 | 접근 실패로 미실행 |
| MU | 403 | 접근 실패로 미실행 |
| TSM | 403 | 접근 실패로 미실행 |
| NVDA | 403 | 접근 실패로 미실행 |
| AMD | 200 | no_official_future_date_found |
| AVGO | 200 | no_official_future_date_found |
| ASML | 403 | 접근 실패로 미실행 |
| LRCX | 200 | no_official_future_date_found |
| MSFT | 200 | no_official_future_date_found |
| META | 403 | 접근 실패로 미실행 |
| AMZN | 403 | 접근 실패로 미실행 |
| GOOGL | 403 | 접근 실패로 미실행 |
| ORCL | 403 | 접근 실패로 미실행 |
| INTC | 200 | no_official_future_date_found |
| AMAT | 403 | 접근 실패로 미실행 |
| 8035 | 200 | 2026-10-30 |
| 042700 | 404 | 접근 실패로 미실행 |
| VRT | 403 | 접근 실패로 미실행 |
| ANET | 403 | 접근 실패로 미실행 |
| SMCI | 403 | 접근 실패로 미실행 |
| VST | 200 | no_official_future_date_found |
| PLTR | 200 | no_official_future_date_found |

사실: 200 8곳, 403 12곳, 404 3곳이었다. HTTP200의 7곳은 `no_official_future_date_found`, 도쿄일렉트론은 공식 문맥의 10/30 날짜를 찾았다. HTTP200이 향후 실적 발표일 검증 완료를 뜻하지 않는다. 날짜 미발견의 원인이 미공개·동적 페이지·파서 범위 중 무엇인지는 이번 관측으로 확정하지 않았다.

사실: 대체 공식 URL을 추가 1회 확인했다. 삼성 [이벤트](https://www.samsung.com/global/ir/ir-events-presentations/events/)는200이며 10/29 날짜가 파싱됐고 [실적 자료](https://www.samsung.com/global/ir/financial-information/earnings-release/)는200이었다. SK하이닉스 [현재 IR](https://www.skhynix.com/ir/UI-FR-IR01/)는200이지만 향후 날짜는 확인하지 못했다. 이 두 회사의 source/calendar URL만 교체했고 생성·만료 시각은 바꾸지 않았다. 한미반도체 공식 홈페이지200만으로 IR 일정 확인을 대신하지 않았으며 기존 IR404는 미해결이다.

해석: 403 응답과 404는 파서 실패와 구별해야 한다. 여러 출처의403이 공급자 자체 장애인지 요청 경로·접근 정책·네트워크에 따른 것인지는 확정하지 않는다. 앞선 로컬 Python 요청은 모두 연결 계층에서 실패했으므로 실제 HTTP/파서 결과와 섞지 않았다.

## 구현과 검증

- earnings/macro 캘린더를 독립적으로 실행하고 한 단계가 실패해도 다른 단계와 결과 빌드를 계속한다. 결과 수집도 독립적으로 시도하되, 최종 발행 게이트는 각 단계 `outcome=failure`를 차단한다.
- 출처별 `phase=fetch/parse`, error enum, HTTP 상태만 보존한다. HTTPError 원문·헤더·응답 body·exception 문자열은 진단에 포함하지 않는다. 중첩 URLError의 DNS/TLS/timeout도 분리한다.
- `always()` summary와 7일 보존 artifact에는 공개 calendar 및 정형 checks만 저장한다. job 성공·출처 확인·공개 발행을 혼동하지 않는다.
- global 만료와 실패 출처 health 차단은 유지한다. 이번 실패를 이유로 유효기간을 늘리거나 expired feed를 green으로 바꾸지 않는다.
- Study 단위90개 통과. HTTP 안전 진단, nested 전송 오류, 파서 오류, 첫 macro 실패 후 독립 출처 계속, 부분 실패 expiry 불변, fresh feed의 failed source 차단 및 검증된 URL/날짜 fixture를 포함한다.

## 남은 운영 범위

모든 출처 성공을 공유 global expiry 갱신의 조건으로 두면 일부 공식 사이트의 지속적인 접근 제한이 전체 feed를 계속 stale로 남길 수 있다. 이번 변경은 안전한 실패·원인 노출까지만 처리한다.

다음 설계에서는 출처별 `last_attempt_at`, `last_success_at`, `expires_at`, 실패 이유를 보존하고, 각 이벤트를 해당 원문의 유효기간에 묶는 partial feed 계약이 필요하다. producer와 소비자가 `완전/부분/직전값/만료/검증 불가`를 함께 지원하기 전에는 이 정책을 임의 적용하지 않는다. 독립 publisher·감사·알림 문턱은 그대로 유지한다.

한계: 이 관측은 로컬 네트워크 각1회다. GitHub runner의 접속 결과, 둘째 QA 실행의 최종 단계 상태와 다음 정기 실행은 root가 별도로 확인해야 한다. 실제 계정·FCM·secret은 사용하지 않았다.

## 발행 게이트 실제 범위 대조

2026-10-24 22:45 KST 부근 로컬 산출물에 대해 공개 fetch 없이 직접 실행했다. 기본 `publication_health.py`(cycle+quant)는 `local artifacts ready`로 통과했다. 이는 해당 두 산출물 준비만을 뜻한다.

`publication_health.py --artifacts cycle-latest.json quant-latest.json event-latest.json macro-policy-latest.json`은 event 만료·calendar review incomplete, policy 만료로 실패했다. 전체 Hosting 발행이 준비됐다는 뜻으로 기본 검사 성공을 확대 해석하지 않는다. 만료 시각은 유지됐으며 실수집/두 번째 runner 결과를 확인하기 전에는 전체 성공으로 보고하지 않는다.
