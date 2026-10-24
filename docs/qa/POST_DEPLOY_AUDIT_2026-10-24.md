# 배포 후 독립 신선도 감사 — 2026-10-24

감사 대상 커밋: `d7e9e0e4204959a092c68b1fcaede199bd3e229e`. 시각은 별도 표시가 없으면 UTC다. UTC에 9시간을 더하면 KST다. 이 문서는 공개 자료의 읽기 전용 관측을 기록하며, 실행·재실행·배포·스케줄·코드를 변경하지 않는다.

## 현재 판정

관측을 단계별로 기록한다. 13:39~13:40Z 관측에서 시장 수집의 최종 44개 종목·거시 4개, FRED 8종, 새 Study ID와 종목별 문맥의 운영 소비를 확인했다. **이때의 GitHub 30분 정규 실행은 달성했다고 판정할 수 없었다.** 약 5시간 동안 명목 예약 슬롯 11개에 공개 schedule run이 1개뿐이었다. 이후 13:56~13:57Z 후속 관측에서 오류 집계 불일치와 새 JS/CSS 배포는 실제 해소를 확인했다. 정규 실행 책임자는 기존 외부 wrapper로 복구됐으며, 다음 외부 접수는 추가 관측 대상이다. 이벤트 QA2는 독립 단계·테스트를 통과했지만 최종 수집 완전성 gate 실패를 유지하고 배포를 차단했다.

아래 첫 관측 기록은 수정 없이 당시 사실로 보존하며, 최종 결과는 문서 뒤쪽의 ‘13:39~13:40 최종 관측’에서 구분한다.

| 영역 | 사실 | 해석 | 한계 |
| --- | --- | --- | --- |
| 웹 정적 배포 | 공개 HTML·CSS·JS 세 파일이 로컬 작업본과 바이트 단위로 일치 | 새 FE 정적 배포가 노출됨 | 실제 승인 사용자 화면·알림·저장소는 열람하지 않음 |
| 시장 수집 | push run `40078392851` 진행 중, `stocks/data.collection_status=running` | 수집 시작과 중간 발행 확인 | job 성공·최종 완전 성공은 아직 확정 불가 |
| 보충 거시 지표 | FRED 8종이 원천 관측일·수집일·다음 점검일·최대 60개 history와 함께 발행됨 | 새 수집·표현 계약이 운영에서 작동 | `fresh`는 각 발표주기 허용 시차 기준이며 당일 발표라는 의미가 아님 |
| 이벤트 실행 | push run `40078392853`은 공식 실적 일정 탐색 단계에서 실패 | 후속 공개 발행은 수행되지 않음 | 로그·원문을 열지 않았으므로 구체적 공급자 장애 원인은 검증 불가 |
| Study 소비 | 당시 `cycle_report.report_id=null`, quality unavailable | 만료된 공개 보고서를 정상 근거로 사용하지 않음 | 새 Study 직접 배포 후 재대조 필요 |

## 실행 시각 대조

관측 대상은 [공개 Actions](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions) API의 run 메타데이터 및 jobs 메타데이터다. run의 접수 시각, run 시작, runner job 시작, 수집 단계 시작, job 종료를 구분한다.

| 실행 | 이벤트/커밋 | 접수(created) | run 시작 | runner job 시작 | 주요 단계 | 종료·판정 |
| --- | --- | --- | --- | --- | --- | --- |
| [시장 40078392851](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40078392851) | push / d7e9e0e | 08:31:46 | 08:31:46 | 08:31:48 | `Run signal checker` 08:32:06 시작 | 첫 관측에서 진행 중 |
| [이벤트 40078392853](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40078392853) | push / d7e9e0e | 08:31:46 | 08:31:46 | 08:31:48 | 공식 실적 일정 탐색 08:31:52~08:32:21 실패 | job 08:32:23 종료 / failure |
| [이전 시장 40071962826](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40071962826) | workflow_dispatch / 6917444 | 06:53:11 | 06:53:11 | 이번 감사 범위 미조회 | 이전 회차 | run updated 07:01:26 / success |
| [이전 주간 40043371748](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40043371748) | schedule / cd7c6fe | 00:07:22 | 00:07:22 | 이번 감사 범위 미조회 | 이전 주간 회차 | run updated 00:07:40 / success |

사실: 새 두 workflow의 실제 runner 시작과 이전 dispatch/schedule 실행을 각각 확인했다. 이벤트 실패 이후 macro 일정 탐색, 결과 수집, 계약 검증, readiness gate, Hosting 배포, 공개 artifact identity 검증은 모두 skipped였다.

해석: workflow 파일의 존재나 push 성공은 예약 실행 성공 또는 모든 공급자 정상의 증거가 아니다. 이전 workflow success 역시 새 코드의 예약 성공을 입증하지 않는다.

한계: 새 커밋을 사용하는 첫 schedule 실행은 첫 관측 시점에 아직 확인되지 않았다. `updated_at`은 run 메타데이터 갱신 시각이므로 job 종료와 혼동하지 않는다.

## 공개 데이터와 원천 관측일

허용 범위인 [stocks/data](https://firestore.googleapis.com/v1/projects/stock-sayo/databases/(default)/documents/stocks/data) 및 [stocks/last_good](https://firestore.googleapis.com/v1/projects/stock-sayo/databases/(default)/documents/stocks/last_good)만 무인증으로 읽었다. 다른 Firestore 문서·사용자·FCM·관리자 자료는 조회하지 않았다.

첫 `stocks/data` 관측의 Firestore updateTime은 `2026-10-24T08:32:14.447453Z`, `collection_status=running`이었다. FRED context generated_at은 `2026-10-24T17:32:13+09:00`이었다. 화면용 `updated`는 이전 회차의 `10월 24일 16:01`을 유지했다. `stocks/last_good`의 updateTime은 `2026-10-24T07:01:21.322818Z`, status success였으며 아직 새 market_environment 필드는 없었다.

사실: 중간 발행에서 새 거시 문맥과 직전 회차의 종목·표시 문자열이 함께 존재한다. collection_summary는 수집 도중의 0건과 Study invalid_response 3건을 담았다.

해석: Firestore 문서 write만으로 전체 주식 수집이 끝났다고 판단할 수 없다. 중간 0건은 공급자 실패 확정의 근거가 아니며, 최종 collected_counts와 상태를 확인해야 한다.

| 지표 | 원천 관측일/기준월 | 상태 | 조회 성공 시각(KST) | 다음 조회(KST) |
| --- | --- | --- | --- | --- |
| 미국 2년 DGS2 | 2026-10-22 | fresh | 10-24 17:32:12 | 10-24 23:32:12 |
| 미국 실질 10년 DFII10 | 2026-10-22 | fresh | 10-24 17:32:12 | 10-24 23:32:12 |
| 광의 달러 DTWEXBGS | 2026-10-16 | fresh | 10-24 17:32:12 | 10-25 17:32:12 |
| 금융여건 NFCI | 2026-10-16 | fresh | 10-24 17:32:12 | 10-25 17:32:12 |
| 신규 실업수당 ICSA | 2026-10-17 | fresh | 10-24 17:32:12 | 10-25 17:32:12 |
| 근원 CPI CPILFESL | 2026-09 기준월 | fresh | 10-24 17:32:12 | 10-25 17:32:12 |
| 근원 PCE PCEPILFE | 2026-08 기준월 | fresh | 10-24 17:32:12 | 10-25 17:32:12 |
| 실업률 UNRATE | 2026-09 기준월 | fresh | 10-24 17:32:12 | 10-25 17:32:12 |

일별 원천 값과 월별 기준월이 실제 조회 시각으로 덮이지 않았다. 8종 모두 last_attempt_status success 및 last_checked_at/last_success_at을 가졌다. 시장 4개 지표는 VIX·QQQ 2026-10-23 종가, KOSPI 2026-10-23T20:15:00+09:00, USD/KRW 2026-10-24T12:01:51+09:00의 공급자 시각을 노출했다. 당시 상태는 VIX/QQQ/KOSPI market_closed, 환율 fresh였다. 공급자 시각 자체의 의미를 거래소 체결시각으로 추가 해석하지 않는다.

## 배포 FE의 계약 소비

공개 [HTML](https://stock-sayo.web.app/), [CSS](https://stock-sayo.web.app/assets/sayo-ui.css), [JS](https://stock-sayo.web.app/assets/sayo-ui.js)를 각각 한 번 읽어 비교했다.

| 자산 | bytes | SHA-256 | 로컬 일치 |
| --- | --- | --- | --- |
| HTML | 140544 | f9da3c486e9032cd4cb47e7b1809d94ca199ee096f43be11af90f175d1b6167e | 예 |
| CSS | 15251 | cfb43bf0bd066ed86b0a1fd94390cf00ee036296698c31863990d16848a0298e | 예 |
| JS | 20359 | 87dc30bc131963aac4f515451d35ffe79d09f9f1f3c6f95ae3cf6db2452d78ee | 예 |

사실: 다운로드한 배포 JS의 순수 함수에 실제 공개 stocks/data를 넣었다. 8개 지표 모두 fresh로 해석했고 history 60개 및 chart model 생성에 성공했다. CPI/PCE/실업률의 관측 표시는 각각 월 기준으로 해석했다. 이 검증은 createSayoUI를 실행하지 않아 localStorage·로그인·푸시 상태를 읽지 않는다.

해석: backend 생산 필드와 배포된 FE의 값·그래프 계약은 호환된다.

한계: 실제 사용자 인증 화면, 최초 Firestore 응답, DOM 렌더 지연과 그래프 팝업의 시각적 표시를 측정한 결과는 아니다. 이번 실제 거시 자료가 모두 유효하므로 운영 데이터만으로 unknown/timing 보류 사례를 직접 관측하지는 못했다. 해당 분기는 별도 mock 회귀 검증으로 확인했으며 운영 성공과 구분한다.

## Study 새 문맥과 발견한 모순

첫 관측에서 공개 [cycle-latest](https://stock-sayo-study.web.app/data/cycle-latest.json)의 ID는 `semiconductor-cycle-2026-09-19-1400-weekly`, generated_at은 `2026-09-19T14:00:21+09:00`, expires_at은 `2026-09-27T14:00:21+09:00`이었다. 공개 policy의 ID는 `us-rate-policy-warsh-jackson-hole-2026-08-28`, expires_at은 `2026-09-17T06:00:00+09:00`이었다. 연구 본문·근거 원문을 감사 문서에 복사하지 않았다.

사실: stocks/data의 cycle_report는 report_id null / quality_status unavailable이었다. 이 중간 payload에는 cycle data_status가 없었다. 배포 JS의 studyReportMeta 결과는 quality unavailable이지만 사용자 안내문은 일반적인 ‘종목별 사이클과 근거를 펼쳐 확인하세요’였다.

해석: 만료된 보고서 차단은 작동한다. 반면 data_status가 없는 기존·중간 payload에서 FE가 연결 불가를 명시하지 않는 표시 공백이 있다. 새 Study 배포 및 최종 수집의 data_status 추가 여부를 재대조해야 한다.

한계: Study 직접 배포는 root 작업 중이며 이 감사가 실행하지 않았다. 새 보고서 ID와 종목별 assessment/data status·coverage·hypotheses·검토/다음 점검 시각의 운영 소비는 후속 관측에서 판정한다.

## 08:36 재관측

공개 endpoints를 두 번째로 조회한 시각은 `2026-10-24T08:36:10Z`다. 시장 run은 여전히 in_progress였다. stocks/data의 updateTime은 `08:34:38.198881Z`, collection_status running, 수집 수는 한국 주식 7/7·거시 4/4·한국 ETF 0/15·미국 주식 0/22였다. 진행 중 0건을 공급자 최종 실패로 판정하지 않는다. last_good updateTime은 그대로 07:01:21이었다.

새 공개 Study report ID는 `semiconductor-cycle-2026-10-24-1730-weekly`, generated_at `17:30:35+09:00`, expires_at `2026-11-01T17:30:35+09:00`, quality gate passed였다. NVDA·AVGO·AMD는 data_status current / assessment insufficient이며 context_evidence_ids 1개가 실제 report evidence ID와 연결된다. 삼성전자 등은 data_status expired / assessment insufficient로, 새 생성 시각을 갖는 보고서 안에서도 근거의 만료를 구분한다. company 항목에는 coverage 필드가 없고 segment에는 publisher/pillar/evidence coverage가 있다.

해석: 새 Study 문서 생성은 검증됐지만 당시 stocks/data의 cycle_report.report_id는 여전히 null이다. 현재 collector는 새 Study 공개 전에 초기 조회를 마쳤기 때문에 새 문맥의 운영 소비는 후속 수집에서 확인해야 한다. 보고서 gate passed는 모든 segment의 근거 충분·최신을 의미하지 않는다.

### 오류 집계의 독립 불일치

사실: 기존 last_good의 collection_summary.error_count는 3인데 errors_by_provider의 값 합계는 13이다. errors_by_provider에는 FnGuide 7, KIS 2, Yahoo 1, Study 3이 남아 있고, 당시 data 중간본은 Study 3만 있어 합계 3과 일치한다.

해석: 현재 last_good의 provider별 카운트를 모두 같은 회차의 오류라고 단정할 수 없다. 정적 코드상 last_good은 final payload를 `set(..., merge=True)`로 쓰므로, 과거 nested map 키의 잔존 가능성이 있다. 문서 시각·process success와 개별 provider 정상 여부를 별개로 판정해야 한다.

한계: 감사는 쓰기나 재현용 Firestore 접근을 수행하지 않는다. 다음 final data/last_good 비교 또는 별도의 mock·emulator 검증이 있어야 원인을 확정할 수 있다.

### 첫 관측 후 수정과 검증 범위

root는 수집 진행/부분/실패 안내와 Study unavailable 안내를 추가 수정했다고 알렸다. 이 보고서의 최초 SHA-256 일치 결과는 최초 관측 당시 작업본과의 대조다. 추가 수정의 공개 노출 여부는 새 정적 자산을 다시 받아 독립 확인해야 한다.

## 13:39~13:40 최종 관측

### 정규 실행: 1회 실증, 30분 연속성 미달

대상: [market-data workflow의 공개 최근 100회 조회](https://api.github.com/repos/insightnexusofficial-maker/stock-signal-agent/actions/workflows/market-data.yml/runs?per_page=100), [workflow 등록 정보](https://api.github.com/repos/insightnexusofficial-maker/stock-signal-agent/actions/workflows/market-data.yml), [전체 최근 100회](https://api.github.com/repos/insightnexusofficial-maker/stock-signal-agent/actions/runs?per_page=100).

사실: market workflow는 state active, created_at `08:31:44Z`다. cron은 매시 7·37분이다. 최근 100회 요청의 실제 응답은 total_count 2, 배열 2개였으며 push 1회와 schedule 1회뿐이었다. 전체 실행 최근 100회에서도 새 market 실행은 이 둘뿐이고, 별도 실패·취소·dispatch 실행은 보이지 않았다.

| 실행 | 접수 | run 시작 | runner job 시작 | 수집 단계 | runner job 종료 | 결론 |
| --- | --- | --- | --- | --- | --- | --- |
| push 40078392851 | 08:31:46 | 08:31:46 | 08:31:48 | 08:32:06~08:39:37 | 08:39:40 | success |
| [schedule 40095821413](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40095821413) | 12:53:41 | 12:53:41 | 12:53:44 | 12:54:05~13:02:00 | 13:02:04 | success |

08:37부터13:37까지 명목 슬롯은 08:37, 09:07, 09:37, 10:07, 10:37, 11:07, 11:37, 12:07, 12:37, 13:07, 13:37의 11개다. 실제 schedule 접수는 12:53:41 한 번이다. 가장 가까운 앞선 12:37 슬롯과도 16분41초 차이가 난다. runner 접수 후 시작 지연은 3초였고 수집 단계는 7분55초였다. 수집 소요가 30분을 초과해 적체됐다는 증거는 없다.

해석: 예약 경로가 한 번 실제 작동했다는 증거는 있지만 30분 주기 달성, 정규 실행 연속성, 누락 없음의 증거는 아니다. 11개 명목 슬롯을 특정 run ID와 일대일 대응시킬 자료가 없으므로 ‘10회가 영구 유실됐다’고 확정하지 않는다. 마지막 수집 완료 13:01:57부터 최종 조회 13:39:25까지 37분28초가 지났다.

한계: 공개 run 메타데이터에는 scheduler 내부 접수 대기열이나 누락 원인이 없다. GitHub의 지연·미접수·기타 원인을 확정할 수 없다. [Schedule Health 40097955258](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40097955258)는 13:23:51 접수 후 success였으나 그 process success를 시장 수집 주기 정상으로 해석하지 않는다. 이번 감사는 재dispatch·schedule 변경을 수행하지 않았다.

### 최종 stocks/data와 last_good

| 항목 | stocks/data | stocks/last_good |
| --- | --- | --- |
| Firestore updateTime | 13:01:58.574126Z | 13:01:59.495084Z |
| 마지막 시도 | 21:54:06 KST | 동일 |
| 수집 종료 | 22:01:57 KST | 동일 |
| 화면 updated | 10월 24일 22:01 | 동일 |
| collection_status | success | success |
| 수집 수 | 한국 주식7/7, 한국 ETF15/15, 미국 주식22/22, 거시4/4 | 동일 |
| critical_error_count | 0 | 0 |
| error_count | 2 | 2 |
| provider map 합계 | 2 | 12 |
| event_sync_status | unavailable | unavailable |

사실: data 최종 summary의 오류는 Study event_feed와 macro_policy_signal 각각 invalid_response 1건, 합계 2다. cycle 오류는 새 회차에서 사라졌으며 새 보고서가 소비됐다. last_good에는 같은 error_count 2와 함께 이전 FnGuide7/KIS2/Yahoo1 키가 남아 provider 합계 12, category 합계 13으로 불일치한다. 서로 다른 오류 집계 map도 서로 일치하지 않는다.

해석: 시장 종목 개수와 주요 거시 수집 완성은 확인된다. 이벤트·정책 문맥까지 정상이라는 뜻은 아니다. last_good의 provider/category별 과거 잔존 값을 새 회차 공급자 실패로 사용해서는 안 된다. nested map 잔존 가설은 두 회차의 원천 카운터 변화와 정적 merge 경로에 부합하지만, 원인 확정·수정 검증은 별도 담당 작업이다.

### FRED 캐시·원천 시각과 기업 타이밍

사실: 최종 market_environment generated_at은 21:54:12 KST, quality_status complete다. 8개 지표 모두 fresh 및 최초 수집의 관측일을 유지했다. 마지막 체크/성공/조회 시각은 모두17:32:12 KST 그대로이며, 다음 체크는 금리2종23:32:12, 나머지6종 다음날17:32:12다. 다음 조회 전의 새 정규 회차가 기존 캐시를 재사용한 결과다. 새 환경 generated_at을 지표의 last_success나 observed_at으로 덮지 않았다.

핵심 거시4종은21:54:11 KST에 재조회됐다. VIX·QQQ·KOSPI는 market_closed, USD/KRW fresh로 생산됐고 지역 mode는 미국/한국 모두 adjust다. 공개44개 종목 모두 timing_status available이었다. 자료가 유효한 이번 회차에서 unknown을 발생시킨 사례는 없다.

해석: 신규 지표의 캐시 TTL 및 원천 시각 보존은 실제 다음 회차에서도 작동한다. 기업 근거 부족·만료는 거시 타이밍 unavailable과 다른 상태다. 승인된 설계상 Study 문맥은 표시 전용이므로 삼성의 cycle_data_status expired와 timing_status available 공존 자체를 매수 기준 오류로 해석하지 않는다.

한계: unknown 시 candidate 유지/timing 보류는 기존 mock 회귀 검증에서 확인했으며, 이번 운영 자료는 그 실패 분기를 실증하지 않는다. 실제 공급자 실패를 임의로 만들어 운영 신호를 변경하지 않았다.

### 새 Study 보고서와 배포 FE 소비

사실: 공개 Study, data, last_good 모두 report ID `semiconductor-cycle-2026-10-24-1730-weekly` 및 generated17:30:35/만료11-01 17:30:35 KST가 일치한다. stocks/data report metadata는 data_status fresh / quality_status passed다. NVDA·AMD는 current/insufficient, context evidence1개·가설3개·마지막 검토17:27:52·다음 점검10-31 17:30:35를 실제 소비했다. 삼성은 expired/insufficient, evidence0개·가설3개·마지막 검토 null로 구분됐다.

최종 공개 JS는 bytes20359, SHA-256 `87dc30bc131963aac4f515451d35ffe79d09f9f1f3c6f95ae3cf6db2452d78ee`로 첫 관측과 동일했다. 다운로드한 배포 모듈의 순수 함수에 최종 공개 payload를 넣으면 Study 안내는 ‘최신 관측 · 검토 완료’, NVDA·AMD 라벨은 ‘사이클 중립 · 근거 부족’, 삼성 라벨은 ‘사이클 갱신 대기’로 해석됐다. 8종 지표 모두 올바른 일자/기준월·60개 history·그래프 model을 소비했다.

해석: 새 cycle ID와 증거 문맥의 backend→배포 JS 계약 소비는 확인된다. 전체 report의 최신 발행과 개별 기업의 근거 부족/만료도 구분된다. 첫 관측의 중간 payload 표시 공백은 root가 추가 수정 중이지만 최종 공개 JS에는 아직 반영되지 않았다.

한계: 실제 계정 접근·DOM·계정 저장소·푸시 토큰을 읽거나 조작하지 않았다. 새 JS 재배포 후 변경된 안내를 다시 확인할 필요가 있다.

## 남은 관측 공백과 후속 확인

- 30분 정규 실행의 연속성이 현재 공개 관측에서 충족되지 않는다. 예약 내부 누락/지연 원인은 검증 불가다.
- 이벤트 workflow 실패 후 검증·배포는 실행되지 않았고 data 이벤트/정책 상태도 unavailable이다. 실패 원인은 별도 조사 결과와 구분한다.
- last_good nested 오류 집계의 불일치가 남아 있다. 수정 후 실제 다음 회차 data/last_good 집계 합계가 일치하는지 확인해야 한다.
- 추가 수정된 JS의 공개 배포와 중간/부분/실패 상태 안내는 아직 독립 확인 전이다.

## 13:56~13:57 후속 QA2 관측

이 절은 앞선 ‘남은 관측 공백’을 후속 관측으로 갱신한다. 앞선 실패 기록은 당시 사실로 보존한다.

### 최종 집계 수정의 실제 발행 확인

대상: [시장 QA2 run40099571560](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40099571560), 공개 data/last_good.

사실: 커밋 `318eb29b82eb6a5130f8e75e0be81a4748ac8bd0`, push 접수13:46:17Z, runner 시작13:46:19Z, 수집 단계13:46:37~13:55:05Z, job 종료13:55:07Z, run updated13:55:08Z, conclusion success다. data updateTime13:55:03.895856Z, last_good13:55:04.795939Z이며 두 문서 모두 수집 종료22:55:03 KST·status success·수집수7/15/22/4다.

두 문서 각각 error_count6, errors_by_provider 합계6, errors_by_stage 합계6, errors_by_category 합계6으로 일치했다. 실제 오류는 FnGuide timeout4와 Study invalid_response2로 구분된다. 이전 KIS/Yahoo 오류 키는 이번 최종 집계에 남아 있지 않았다.

해석: nested 오류 map의 회차 간 잔존 불일치는 실제 운영 최종 두 문서에서 해소됐다. 종목 개수 완성과 공급자별 fallback/부가자료 오류는 여전히 구분해야 한다. Study 이벤트/정책 상태 unavailable은 이번에도 남아 있다.

### 추가 FE 배포 확인

| 자산 | bytes | SHA-256 | 현재 로컬 일치 |
| --- | --- | --- | --- |
| JS | 20944 | 7e974b5ee4a8e5a801447c6a88630f0cbf79fb28ec17487cc2df962d60ea65b1 | 예 |
| CSS | 15282 | 2dfe732141823eba92d54789cf4a77332c11db0c23fa154b6a4e0969625c8fd1 | 예 |

사실: 후속 공개 자산을 새로 읽어 바이트 비교했다. 배포 JS의 순수 studyReportMeta 함수에 quality unavailable·status 없는 기존 계약을 넣으면 ‘확인 불가 · 확인 불가’로 표시된다. 앞선 일반 안내로의 누락은 수정됐다.

한계: 실제 계정 화면·DOM·저장소·푸시 수신은 접근하지 않았다. 수집 상태 안내의 코드 배포는 확인했으나 실제 장애를 주입하여 운영 UI를 변경하지 않았다.

### Study 정량 시각과 사이클

사실: 공개 [quant-latest](https://stock-sayo-study.web.app/data/quant-latest.json)의 generated_at은22:44:46 KST, source_collection_finished_at은22:01:57 KST, source_collection_status success다. 새 수집 QA2의22:55:03 완료보다 앞서 발행된 정량본이다. cycle ID는 data/last_good 모두 `semiconductor-cycle-2026-10-24-1730-weekly`로 유지된다.

해석: 정량 산출물의 생성 시각과 원천 회차 시각은 구분된다.22:44 발행본을22:55 시장 회차를 소비한 것으로 해석하지 않는다. 원천이 완료 상태였다는 계약은 확인된다.

### 이벤트 QA2: 실패를 유지하고 진단만 보존

대상: [이벤트 QA2 run40099571558](https://github.com/insightnexusofficial-maker/stock-signal-agent/actions/runs/40099571558).

사실: 접수13:46:17Z, job 시작13:46:19Z, 종료13:46:58Z, conclusion failure다. 공식 실적 일정 탐색·거시 일정 탐색·거시 결과 단계·이벤트 반영·정량 동기화·feed build·policy build·테스트/정적 검증 단계는 success였다. `Require independent collection stages to complete`가13:46:55Z failure였고 이후 Hosting readiness·snapshot·배포·공개 identity·알림은 skipped였다. 안전 진단 기록과 artifact 보존 단계는 success였다.

공개 artifact 메타데이터는 ID12561839465, 이름 `official-calendar-checks-40099571558`, bytes5369, created13:46:56Z, expired false다. artifact 원문·API 응답 본문은 읽지 않았다.

해석: 모든 독립 단계를 실행한 개선은 확인되지만 각 단계 process success를 자료 완전성으로 위장하지 않는다. 최종 완전성 검증 실패는 유지되며 불완전한 결과의 Hosting 배포를 차단했다. 구체적인 누락 공급자·원문 실패 원인은 이 메타데이터만으로 확정하지 않는다.

### 정규 실행 책임자 복구 상태

사실: [기존 wrapper259588964](https://api.github.com/repos/insightnexusofficial-maker/stock-signal-agent/actions/workflows/259588964)는 path `.github/workflows/check_signals.yml`, state active, updated13:53:10Z다. 현재 저장소의 wrapper는 workflow_dispatch만 제공하고 reusable market-data를 호출한다. market-data의 수집 schedule은 제거됐다. 공개 wrapper 실행 목록의 가장 최근 실행은13:57Z 이전까지 과거06:53 dispatch였다.

해석: 기존 외부 호출 경로의 등록 복구는 확인된다. 외부 scheduler의 실제 설정·404 실패는 이번 감사가 직접 읽지 않았으므로 root/사용자의 제공 증거와 구분한다. wrapper 존재만으로 다음 정규 접수 정상이나 외부 스케줄 변경 완료를 확정하지 않는다. 다음23:00 KST 전후의 공개 workflow_dispatch 접수를 추가 대조한다.

### 23:00 정규 접수 관측 결과

사실: KST23:00:27 및23:01:34(UTC14:00:27/14:01:34)에 wrapper259588964의 공개 최근 실행을 두 차례 조회했다. 두 번째 요청은 no-cache 헤더를 사용했다. 두 번 모두 가장 최근 실행은 이전06:53:11Z의 run40071962826이었으며, 복구 커밋 이후 새 workflow_dispatch는 보이지 않았다.

해석: wrapper active 복구와 실제 정규 접수 복구는 구분된다.23:01:34까지 정상 외부 접수를 실증하지 못했다. 정각 직후1분34초라는 제한된 관측이므로 앞으로 늦게 도착할 가능성을 배제하지 않지만, 운영 정규화 완료라고 보고할 근거는 없다.

한계: 외부 scheduler 로그·설정·인증은 접근하지 않았다. HTTP404, 미접수, 권한, scheduler 지연 중 구체적 원인을 이 공개 run 목록만으로 확정할 수 없다. 감사가 실제 dispatch를 보내거나 재실행하지 않았다.


## 사용자 확인으로 보정한 외부 호출 기대 범위

23시경 사용자는 기존 외부 cron의 실패 HTTP상태가404이며, 기존 실행 규칙은 **국장·미장이 열려 있을 때만30분 간격**이라고 확인했다. 이는 도구가 외부 관리 화면을 읽은 관측이 아니라 사용자가 제공한 운영 정보다. 토요일23시 미접수는 해당 규칙상 정상이며 외부 cron 장애로 판정하지 않는다. 앞선 GitHub24시간cron 실험의11개 명목슬롯/실제1회 관측과도 다른 기대 범위다.

기존 wrapper 경로 복구 및 GitHub 증시 schedule 제거는 공개 코드와 active 등록으로 확인했다. 다음 실제 정규장 호출·외부 작업 활성 상태는 이번 시간대에 검증하지 못했다. root는 누락 감시의 기대 범위를 정규장·DST·장시작 유예로 수정한다. 이 보정은 웹/수집/Study의 앞선 직접 관측 결과를 바꾸지 않는다.


최종 정규장 감사 코드는 공개 wrapper 이력에 대해 현재 `outside_session`, `overdue=false`, CLI exit0을 반환했다. 공개자료가 새로 수집된 것처럼 시각을 변경하지 않았다. 각 시장의 현지 요일을 사용하며 KST토요일03:00/New York금요일14:00에는 미국장이 열려 있는 것으로 유지한다. 사용자 확인 이후의 현재 토요일 밤 미접수를 누락으로 판정하지 않는다. 공식 휴일 목록 미제공·조기폐장·다음 정규장 실제 접수는 여전히 확인 공백이다.
