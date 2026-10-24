// Presentation only: all market and investment decisions come from the producer.
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const number = value => typeof value !== "boolean" && value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
const labels = {delayed:"관측 지연",current:"현재 근거",latest_available:"최근 발표값",complete:"자료 확인 완료",partial:"일부 자료 확인 필요",success:"동기화 완료",passed:"검토 완료",fresh:"최신 관측",market_closed:"최근 종가",stale:"직전값",expired:"만료",unavailable:"확인 불가",unknown:"확인 불가",pending:"검토 대기",insufficient:"근거 부족"};
export function normalizeHistory(history) {
  const points = new Map();
  for (const point of Array.isArray(history) ? history : []) {
    if (!point || typeof point !== "object") continue;
    const date = String(point.date || point.at || point.observed_at || "");
    const value = number(point.value ?? point.close);
    if (date && Number.isFinite(Date.parse(date)) && value !== null) points.set(date, {date, value});
  }
  return [...points.values()].sort((a,b) => Date.parse(a.date)-Date.parse(b.date)).slice(-60);
}
export function metricState(metric) {
  if (!metric || number(metric.current ?? metric.price ?? metric.value) === null) return "unavailable";
  if (["expired", "delayed"].includes(metric.data_status || metric.freshness_status)) return metric.data_status || metric.freshness_status;
  if (metric.is_stale && metric.data_status !== "latest_available") return "stale";
  const state = metric.data_status || metric.freshness_status;
  if (state) return Object.hasOwn(labels,state) ? state : "unknown";
  return "unknown";
}
export function chartModel(history, width=560, height=220) {
  const points=normalizeHistory(history);
  if(points.length<2) return null;
  const values=points.map(p=>p.value),low=Math.min(...values),high=Math.max(...values);
  const spread=high-low || Math.max(Math.abs(high)*0.01,1);
  const minimum=low-spread*0.1, maximum=high+spread*0.1;
  const start=Date.parse(points[0].date),duration=Date.parse(points.at(-1).date)-start || 1;
  return {points,minimum,maximum,path:points.map((p,i)=>`${i?"L":"M"}${(42+(Date.parse(p.date)-start)/duration*(width-58)).toFixed(2)},${(16+(maximum-p.value)/(maximum-minimum)*(height-48)).toFixed(2)}`).join(" ")};
}
function dateText(value) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return value + " 기준";
  if (!value || !Number.isFinite(Date.parse(value))) return "관측 시각 확인 불가";
  return new Intl.DateTimeFormat("ko-KR",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Seoul"}).format(new Date(value))+" KST";
}
export function observationText(metric) {
  if (metric?.observation_precision === "month" && /^\d{4}-\d{2}$/.test(metric.reference_period || "")) return metric.reference_period + " 기준월";
  return dateText(metric?.observed_at || metric?.data_as_of);
}
function unitText(unit) { return ({people:"명",index:"지수","%":"%"})[unit] || unit || ""; }
function valueText(metric) {
  const value=number(metric?.current ?? metric?.price ?? metric?.value);
  return value===null ? "—" : value.toLocaleString("ko-KR",{maximumFractionDigits:2})+(metric.unit==="%"?"%":metric.unit==="people"?"명":"");
}
function safeLink(url) { try { return new URL(url).protocol==="https:" ? url : null; } catch { return null; } }
function sourceHtml(metric) { const url=safeLink(metric?.source_url);return url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(metric.source || "자료 출처")} ↗</a>` : ""; }
function openDialog(dialog, opener) {
  dialog._opener=opener || document.activeElement;
  if (!dialog.open) dialog.showModal();
}
function makeDialog(id,title,body) {
  const dialog=document.createElement("dialog");dialog.id=id;dialog.className="sayo-dialog";dialog.setAttribute("aria-labelledby",`${id}-title`);
  dialog.innerHTML=`<div class="dialog-head"><h2 id="${id}-title">${title}</h2><button type="button" class="dialog-close" aria-label="닫기">✕</button></div>${body}`;
  document.body.append(dialog);
  dialog.querySelector(".dialog-close").addEventListener("click",()=>dialog.close());
  dialog.addEventListener("close",()=>{if(dialog._opener?.isConnected)dialog._opener.focus();});
  dialog.addEventListener("click",event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
  return dialog;
}
export function preferenceStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}
export function studyReportMeta(data) {
  const report=data?.cycle_report || {};
  const status=report.data_status || data?.cycle_sync_status || data?.cycle_quality_status;
  const quality=report.quality_status;
  return {status,quality,summary:status?`${labels[status] || "확인 필요"}${quality?` · ${labels[quality] || "검토 상태 확인"}`:""}`:"종목별 사이클과 근거를 펼쳐 확인하세요.",generated_at:report.generated_at || data?.cycle_generated_at,expires_at:report.expires_at};
}
export function cycleLabel(stock) {
  const base={favorable:"사이클 우호",neutral:"사이클 중립",caution:"사이클 주의",pending:"사이클 갱신 대기"};
  const status=stock?.cycle_status;
  if(!base[status]) return null;
  if(stock.cycle_data_status && !["fresh","market_closed","current","latest_available"].includes(stock.cycle_data_status)) return "사이클 갱신 대기";
  const reasons={insufficient:"근거 부족",conflicting:"근거 상충",awaiting_independent_review:"독립 검토 대기"};
  return base[status]+(status==="neutral"&&reasons[stock.cycle_assessment_status]?` · ${reasons[stock.cycle_assessment_status]}`:"");
}
export function renderCycleContext(stock) {
  const hypotheses=Array.isArray(stock.cycle_hypotheses)?stock.cycle_hypotheses:[];
  const changes=Array.isArray(stock.cycle_changes)?stock.cycle_changes:[];
  const evidence=stock.cycle_evidence || [];
  const refs=Array.isArray(evidence)?evidence:[...(Array.isArray(evidence.supporting)?evidence.supporting:[]),...(Array.isArray(evidence.contrary)?evidence.contrary:[])];
  const statusNames={favorable:"우호",neutral:"중립",caution:"주의",pending:"검토 대기",sufficient:"근거 충족",conflicting:"근거 상충",insufficient:"근거 부족",awaiting_independent_review:"독립 검토 대기"};
  const text=item=>{
    if(typeof item==="string")return item;
    if(!item || typeof item!=="object")return "";
    const description=item.summary||item.fact||item.question||item.label;
    if(description)return description;
    if(item.from||item.to){const before=statusNames[item.from]||item.from||"이전 판정 없음",after=statusNames[item.to]||item.to||"판정 대기";const assessment=item.assessment_to?` · ${statusNames[item.assessment_to]||item.assessment_to}`:"";return `${before} → ${after}${assessment}`;}
    return "";
  };
  const visibleChanges=changes.map(text).filter(Boolean).slice(0,4),visibleHypotheses=hypotheses.map(text).filter(Boolean).slice(0,4);
  const safeRefs=refs.filter(item=>item&&typeof item==="object").slice(0,6).map(item=>{const url=safeLink(item.source_url||item.url),direction=item.direction==="negative"?"반대 근거":item.direction==="positive"?"지지 근거":"근거";return `<p><span class="research-evidence-label">${direction}</span> ${escape(text(item))}${url?` <a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(item.source_name||"원문")} ↗</a>`:""}${item.published_at?` <span class="metric-meta">${escape(item.published_at)}</span>`:""}</p>`;}).join("");
  if(!stock.cycle_reason&&!visibleHypotheses.length&&!visibleChanges.length&&!refs.length)return "";
  return `<section class="research-context"><h3>산업·거시 점검</h3>${stock.cycle_reason?`<p>${escape(stock.cycle_reason)}</p>`:""}${visibleChanges.length?`<h4>이번 점검의 변화</h4>${visibleChanges.map(item=>`<p>${escape(item)}</p>`).join("")}`:""}${visibleHypotheses.length?`<h4>확인할 보유 근거</h4>${visibleHypotheses.map(item=>`<p>${escape(item)}</p>`).join("")}`:""}${safeRefs}${stock.cycle_last_reviewed_at?`<p>마지막 검토 ${escape(dateText(stock.cycle_last_reviewed_at))}</p>`:""}${stock.cycle_next_check_at?`<p>다음 확인 ${escape(dateText(stock.cycle_next_check_at))}</p>`:""}</section>`;
}
export function createSayoUI({getPushState,setPushEnabled,storage=preferenceStorage()}={}) {
  let latest={},activeMetric=null;
  const metricNames={vix:"VIX",qqq:"QQQ",kospi:"KOSPI",usdkrw:"USD/KRW",us_2y:"미국 2년 국채금리",us_real_10y:"미국 10년 실질금리",usd_broad:"광의 달러지수",nfci:"금융여건 NFCI",initial_claims:"신규 실업수당",core_cpi:"근원 CPI",core_pce:"근원 PCE",unemployment:"실업률"};
  const preferenceKey="sayo:theme";
  const applyTheme=theme=>{
    document.documentElement.dataset.theme=theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content",theme==="light"?"#f7f5f1":"#221f1d");
    try{storage?.setItem(preferenceKey,theme);}catch{}
  };
  try{applyTheme(storage?.getItem(preferenceKey)==="light"?"light":"dark");}catch{applyTheme("dark");}
  const settings=makeDialog("settings-dialog","설정",`<div class="settings-row"><div><h3>어두운 화면</h3><p>끄면 밝은 화면으로 전환합니다.</p></div><input id="theme-toggle" type="checkbox" role="switch" aria-label="어두운 화면" checked></div><div class="settings-row"><div><h3>푸시 알림</h3><p id="push-setting-description">알림 설정을 확인합니다.</p></div><input id="push-toggle" type="checkbox" role="switch" aria-label="푸시 알림"></div><p id="settings-message" class="settings-message" role="status" aria-live="polite"></p>`);
  const themeToggle=settings.querySelector("#theme-toggle"),pushToggle=settings.querySelector("#push-toggle"),pushDescription=settings.querySelector("#push-setting-description"),message=settings.querySelector("#settings-message");
  themeToggle.addEventListener("change",()=>applyTheme(themeToggle.checked?"dark":"light"));
  async function refreshPush() {
    const state=await getPushState?.() || {registered:false,supported:false,enabled:false};
    pushToggle.checked=state.enabled===true && state.permission==="granted";
    pushToggle.disabled=!state.registered || !state.supported;
    pushDescription.textContent=!state.supported?"이 환경은 웹 푸시를 지원하지 않습니다.":!state.registered?"알림 등록과 승인을 마치면 설정할 수 있습니다.":state.permission!=="granted"?"브라우저 알림 권한이 꺼져 있습니다. 켜려면 브라우저 설정에서 허용해 주세요.":"이 기기의 알림 수신을 설정합니다. 화면 접근 승인은 유지됩니다.";
    return state;
  }
  pushToggle.addEventListener("change",async()=>{
    const desired=pushToggle.checked;pushToggle.disabled=true;message.textContent="알림 설정 저장 중…";
    try{await setPushEnabled?.(desired);message.textContent=desired?"푸시 알림을 켰습니다.":"푸시 알림을 껐습니다. 화면은 계속 사용할 수 있습니다.";}
    catch{pushToggle.checked=!desired;message.textContent="설정을 저장하지 못했습니다. 기존 설정을 유지합니다.";}
    finally{try{await refreshPush();}catch{message.textContent="설정 상태를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.";pushToggle.disabled=true;}}
  });
  document.querySelector("#settings-open")?.addEventListener("click",async event=>{themeToggle.checked=document.documentElement.dataset.theme!=="light";message.textContent="";openDialog(settings,event.currentTarget);try{await refreshPush();}catch{pushToggle.disabled=true;pushDescription.textContent="알림 설정을 확인할 수 없습니다.";}});
  const metricDialog=makeDialog("metric-dialog","지표 상세",`<div class="view-switch" role="group" aria-label="지표 보기 방식"><button type="button" data-view="value" aria-pressed="true">값</button><button type="button" data-view="chart" aria-pressed="false">그래프</button></div><div id="metric-content"></div>`);
  function findMetric(id){return latest[id] || latest.market_environment?.series?.[id] || {};}
  function drawMetric(view="value") {
    const metric=findMetric(activeMetric),state=metricState(metric),points=normalizeHistory(metric.history),model=chartModel(points);
    metricDialog.querySelector("h2").textContent=metricNames[activeMetric] || metric.label || activeMetric;
    metricDialog.querySelectorAll("[data-view]").forEach(button=>{button.setAttribute("aria-pressed",String(button.dataset.view===view));if(button.dataset.view==="chart")button.disabled=!model;});
    const context=`<div class="metric-headline">${escape(valueText(metric))}<span class="data-state ${escape(state)}">${escape(labels[state] || labels.unknown)}</span></div><p class="metric-meta">관측 ${escape(observationText(metric))}</p>${metric.summary?`<p class="metric-explanation">${escape(metric.summary)}</p>`:""}${number(metric.trend_3m_annualized_pct)!==null?`<p class="metric-explanation">3개월 연율 ${escape(Number(metric.trend_3m_annualized_pct).toFixed(2))}%</p>`:""}${number(metric.four_week_average)!==null?`<p class="metric-explanation">4주 평균 ${escape(Number(metric.four_week_average).toLocaleString("ko-KR"))}명</p>`:""}`;
    const chart=model?`<figure class="metric-chart"><svg viewBox="0 0 560 220" role="img" aria-label="${escape(metricNames[activeMetric] || activeMetric)} 관측 추이"><line x1="42" y1="16" x2="42" y2="188" class="chart-axis"/><line x1="42" y1="188" x2="544" y2="188" class="chart-axis"/><path d="${model.path}" class="chart-line"/></svg><p class="chart-range">그래프 범위 ${model.minimum.toLocaleString("ko-KR",{maximumFractionDigits:2})} — ${model.maximum.toLocaleString("ko-KR",{maximumFractionDigits:2})}</p><figcaption>${escape(points[0].date.slice(0,10))} — ${escape(points.at(-1).date.slice(0,10))} · ${points.length}회 관측${metric.unit?` · 단위 ${escape(unitText(metric.unit))}`:""}</figcaption></figure>`:"";
    const table=points.length?`<div class="metric-history"><table><caption>최근 관측값</caption><thead><tr><th scope="col">관측일</th><th scope="col">값${metric.unit?` (${escape(unitText(metric.unit))})`:""}</th></tr></thead><tbody>${[...points].reverse().slice(0,12).map(p=>`<tr><td>${escape(p.date.slice(0,10))}</td><td>${escape(p.value.toLocaleString("ko-KR",{maximumFractionDigits:2}))}</td></tr>`).join("")}</tbody></table></div>`:"";
    metricDialog.querySelector("#metric-content").innerHTML=context+(view==="chart"?chart:table)+(!model?'<p class="metric-meta">비교 가능한 관측값이 2개 이상 쌓이면 그래프를 볼 수 있습니다.</p>':"")+`<div class="metric-source">${sourceHtml(metric)}${metric.retrieved_at?`<span>수집 ${escape(dateText(metric.retrieved_at))}</span>`:""}</div>`;
  }
  metricDialog.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>drawMetric(button.dataset.view)));
  function showMetric(id,opener){activeMetric=id;drawMetric();openDialog(metricDialog,opener);}
  for(const id of ["vix","qqq","kospi","usdkrw"]){const card=document.querySelector(`#macro-${id}`);if(!card)continue;card.setAttribute("role","button");card.tabIndex=0;card.setAttribute("aria-haspopup","dialog");card.setAttribute("aria-label",`${metricNames[id]} 값과 그래프 보기`);card.addEventListener("click",()=>showMetric(id,card));card.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();showMetric(id,card);}});}
  const overview=document.createElement("details");overview.className="market-overview";overview.id="market-overview";overview.innerHTML='<summary><span>거시·Study 점검</span><span id="market-overview-status">자료 확인 중</span></summary><div id="market-overview-content"></div>';
  document.querySelector(".macro-grid")?.insertAdjacentElement("afterend",overview);
  const notice=document.createElement("div");notice.className="data-notice";notice.id="data-notice";notice.hidden=true;notice.setAttribute("role","status");document.querySelector(".macro-grid")?.insertAdjacentElement("beforebegin",notice);
  function update(data) {
    latest=data || {};let pending=0;
    for(const id of ["vix","qqq","kospi","usdkrw"]){
      const metric=findMetric(id),state=metricState(metric),card=document.querySelector(`#macro-${id}`);if(!card)continue;
      if(number(metric.current ?? metric.price ?? metric.value)===null){
        card.querySelector(".macro-value").textContent="—";
        const sub=card.querySelector(".macro-sub");if(sub)sub.textContent="유효한 관측값 없음";
        card.querySelectorAll(".ma-bar").forEach(bar=>bar.hidden=true);
      } else card.querySelectorAll(".ma-bar").forEach(bar=>bar.hidden=false);
      if(!["fresh","market_closed"].includes(state))pending++;
      let note=card.querySelector(".macro-freshness");if(!note){note=document.createElement("div");note.className="macro-freshness";card.append(note);}
      note.textContent=`${labels[state] || labels.unknown} · ${observationText(metric)}`;card.dataset.dataStatus=state;
      if(!["fresh","market_closed"].includes(state)){const badge=card.querySelector(".macro-badge");if(badge){badge.textContent=labels[state] || labels.unknown;badge.className="macro-badge data-state";}}
    }
    notice.hidden=pending===0;notice.textContent=`시장 지표 ${pending}개는 최신 관측 여부를 확인해야 합니다. 각 카드를 눌러 기준 시각을 확인하세요.`;
    const detect=document.querySelector("#detect-status");if(detect){detect.textContent=pending?"일부 자료 확인 필요":"시장 자료 확인 완료";detect.classList.toggle("needs-review",pending>0);}
    const label=document.querySelector(".updated-label");if(label)label.textContent="마지막 수집";
    const env=data.market_environment || {},series=env.series || {},axes=Array.isArray(env.axes)?env.axes:[];
    const cycleReport=studyReportMeta(data);
    const body=document.querySelector("#market-overview-content");
    document.querySelector("#market-overview-status").textContent=env.summary || (["passed","complete"].includes(env.quality_status)?"자료와 변화 확인":"검토 상태 보기");
    body.innerHTML=`${env.summary?`<p class="overview-summary">${escape(env.summary)}</p>`:""}${axes.length?`<ul class="environment-axes">${axes.map(axis=>`<li><span>${escape(axis.label || axis.id)}</span><p>${escape(axis.summary || labels[axis.status] || axis.status || "확인 불가")}</p></li>`).join("")}</ul>`:""}<div class="environment-series">${Object.entries(series).map(([id,metric])=>`<button type="button" class="series-button" data-series="${escape(id)}"><span>${escape(metricNames[id] || metric.label || id)}</span><span class="series-value">${escape(valueText(metric))}</span><small>${escape(labels[metricState(metric)] || labels.unknown)} · ${escape(observationText(metric))}</small></button>`).join("")}</div><div class="study-status"><h3>Study 연결 상태</h3><p>${escape(data.study_summary?.summary || data.research_summary?.summary || cycleReport.summary)}</p>${cycleReport.generated_at || data.cycle_generated_at?`<p class="metric-meta">발행 ${escape(dateText(cycleReport.generated_at || data.cycle_generated_at))}</p>`:""}${cycleReport.expires_at?`<p class="metric-meta">유효 기한 ${escape(dateText(cycleReport.expires_at))}</p>`:""}${data.event_sync_status?`<p class="metric-meta">공식 발표 자료: ${escape(labels[data.event_sync_status] || data.event_sync_status)}</p>`:""}</div>`;
    body.querySelectorAll("[data-series]").forEach(button=>button.addEventListener("click",()=>showMetric(button.dataset.series,button)));
    if(metricDialog.open && activeMetric)drawMetric(metricDialog.querySelector('[data-view][aria-pressed="true"]')?.dataset.view || "value");
  }
  return {update,refreshPush,openSettings:()=>{themeToggle.checked=document.documentElement.dataset.theme!=="light";openDialog(settings);void refreshPush();}};
}
